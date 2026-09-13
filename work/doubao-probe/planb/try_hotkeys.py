"""Plan B: which key gesture does the engine accept as the voice hotkey?

Arms the settings channel ("voice tryout active"), registers a host context on the private
pipe, then injects candidate gestures while watching the engine log for voice activity.

    python try_hotkeys.py scratch
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import sys
import time

from pipe_client import Pipe, pb_int, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window

VK_RMENU, VK_LMENU, VK_SPACE, VK_RCONTROL, VK_CONTROL, VK_SHIFT = 0xA5, 0xA4, 0x20, 0xA3, 0x11, 0x10

CANDIDATES = [
    ("RAlt hold", [VK_RMENU]),
    ("RAlt+Space", [VK_RMENU, VK_SPACE]),
    ("Space", [VK_SPACE]),
    ("LAlt hold", [VK_LMENU]),
    ("RCtrl+Space", [VK_RCONTROL, VK_SPACE]),
    ("Ctrl+Space", [VK_CONTROL, VK_SPACE]),
]

WATCH = re.compile(r"\[VHK\]|voice start|VoiceStart|voice-wave|VoiceStop|voice_key_hook|"
                   r"start voice|voice recall|slot_", re.I)
NOISE = ("samicore", "ttnet", "Cronet", "transport_parameters", "ime_net_sdk")


def arm_tryout() -> None:
    pipe = SettingsPipe()
    print(f"[arm] {pipe.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    pipe.close()


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    log_path = os.path.join(runtime, "pipe_client_server.log")
    ime_id = sys.argv[sys.argv.index("--ime") + 1] if "--ime" in sys.argv else "oime"
    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    arm_tryout()
    foreign = "--foreign-focus" in sys.argv
    hwnd = 0
    if foreign:
        # pretend the *already focused* app is our host (no focus stealing at all)
        u = ctypes.WinDLL("user32")
        u.GetForegroundWindow.restype = wt.HWND
        hwnd = u.GetForegroundWindow()
        pid_of_fg = wt.DWORD(0)
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_of_fg))
        print(f"[info] using the current foreground window 0x{hwnd:X} (pid {pid_of_fg.value})")
    else:
        hwnd = make_foreground_window()
        print(f"[info] host window=0x{hwnd:X}")
        # the hook also requires the foreground app to have Doubao IME selected; do that for
        # this process the same way a TSF-aware app would (per-process, no admin)
        import tsf_activate
        print(f"[tsf] activate profile -> 0x{tsf_activate.activate_doubao_for_process():08X}")

    pipe = Pipe(timeout=35.0)
    host = os.path.basename(sys.executable or "python.exe")
    my_pid = os.getpid()
    focus_pid = pid_of_fg.value if foreign else my_pid
    for op, body in ((0x02, b""),                      # Activate: register this pid as IME-active
                     # ImeChanged: tell the ImeManager that this host now uses the Doubao IME;
                     # the voice hook only fires when the current IME is not "keyboard"
                     (0x11, pb_str(1, "keyboard") + pb_str(2, ime_id) + pb_str(3, "planb")),
                     (0x1B, bytes.fromhex("0801")),
                     # FocusIn carries the requester's pid (and caps) - the engine stores it as
                     # the "focus owner" and the voice hook compares the foreground app with it
                     (0x06, focus_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe")),
                     # RegisterTsfNotifySink(hwnd, cookie): the engine checks the *foreground*
                     # window against the registered host, so this must be our own window
                     (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little")),
                     (0x14, lp("") + lp("") + lp("") + int(hwnd).to_bytes(8, "little") + lp("planb") + lp("python.exe"))):
        res = pipe.call(op, body)
        print(f"[ctx] op=0x{op:02X} -> {res if res is None else res[2]}")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]

    for name, keys in CANDIDATES:
        mark = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        for vk in keys:
            user32.keybd_event(vk, 0, 0, None)
            time.sleep(0.05)
        time.sleep(1.6)
        for vk in reversed(keys):
            user32.keybd_event(vk, 0, 2, None)
        time.sleep(0.8)
        with open(log_path, "rb") as fh:
            fh.seek(mark)
            new = fh.read().decode("utf-8", "replace")
        hits = [l.strip() for l in new.splitlines()
                if l.strip() and WATCH.search(l) and not any(n in l for n in NOISE)]
        print(f"[test] {name:12s} -> {len(hits)} voice-ish lines")
        for line in hits[:14]:
            print("        " + line[:170])
        res = pipe.call(0x17, b"")
        print(f"        peek: {res if res is None else (res[2], res[1].hex())}")

    pipe.close()
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
