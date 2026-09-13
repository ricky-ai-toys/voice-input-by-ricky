"""Plan B: stop the voice session by posting the engine's own message instead of a key-up.

The engine's hook posts `msg = 1007 (PRESS_START)` to its main thread; a clean stop must exist
as a sibling message (VOICE_PRESS_STOP / VOICE_SHOW_WAVE / ...). Our synthetic key-up never
reaches the hook, so this tool enumerates the engine's windows and tries the neighbouring
message ids while watching the log for the stop.

    python stop_by_message.py scratch [--hold 6]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import sys
import time

from pipe_client import Pipe, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_key
from tsf_activate import activate_doubao_for_process

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windows_of(pid: int) -> list[int]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[int] = []

    def cb(hwnd, _lparam):  # noqa: ANN001
        owner = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append(hwnd)
        return True

    user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 6.0
    log_path = os.path.join(runtime, "pipe_client_server.log")

    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    sp = SettingsPipe()
    print(f"[arm] {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()
    hwnd = make_foreground_window()
    print(f"[host] hwnd=0x{hwnd:X} pid={os.getpid()}")
    print(f"[tsf] activate -> 0x{activate_doubao_for_process(verbose=False):08X}")
    pipe = Pipe(timeout=35.0)
    my_pid = os.getpid()
    for op, body in ((0x02, b""),
                     (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb")),
                     (0x1B, bytes.fromhex("0801")),
                     (0x0C, bytes.fromhex("08f8071801")),
                     (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe")),
                     (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little")),
                     (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
                      + lp("planb") + lp("python.exe"))):
        pipe.call(op, body)
    print("[ctx] host context registered")

    wins = windows_of(proc.pid)
    print(f"[win] engine windows: {[hex(w) for w in wins]}")
    send_key(0xA5)
    print("[key] Right Alt down - session should start")
    time.sleep(hold)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    stopped = None
    for msg in range(1006, 1020):
        mark = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        for w in wins:
            user32.PostMessageW(w, msg, 0, 0)
        time.sleep(0.7)
        with open(log_path, "rb") as fh:
            fh.seek(mark)
            new = fh.read().decode("utf-8", "replace")
        hits = [l.strip() for l in new.splitlines()
                if re.search(r"voice stop|VoiceStop|STOP|drain|commit", l) and "samicore" not in l]
        print(f"[try msg={msg}] new lines: {len(hits)}")
        for line in hits[:2]:
            print("    " + line[:170])
        if hits:
            stopped = msg
            break
    print(f"[result] stop message candidate: {stopped}")
    time.sleep(3.0)
    res = pipe.call(0x17, b"\x00\x00\x00\x00")
    print(f"[peek] {res}")
    send_key(0xA5, up=True)
    pipe.close()
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
