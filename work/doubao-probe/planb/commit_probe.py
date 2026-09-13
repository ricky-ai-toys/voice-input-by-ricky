"""Plan B: try to make the engine *commit* the transcript and hand it to a client.

The engine's click-stop path is "finalize in place, drop server final" - it commits whatever the
ASR has already produced. So: start a session, watch the engine log until the ASR yields
non-empty interim text, click to stop at that moment, then poll `PeekVoiceCommit` and the
engine log for a commit.

Keys are always released at the end (`release_all`), so an aborted run cannot leave Alt stuck.

    python commit_probe.py scratch [--wav path] [--timeout 20]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import time

from pipe_client import Pipe, pb_int, pb_str, start_server
from release_stuck_keys import RELEASE, down_keys
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_click, send_key
from tsf_activate import activate_doubao_for_process

FFPLAY = r"E:\ffmpeg\bin\ffplay.exe"
DEFAULT_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "audio", "three_zh_16k.wav")


def release_all() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong,
                                   ctypes.c_void_p]
    for vk, flags in RELEASE:
        user32.keybd_event(vk, 0, flags, None)
        time.sleep(0.02)
    time.sleep(0.1)
    held = down_keys()
    if held:
        print(f"[safety] keys still held after release: {[hex(v) for v in held]}")
    else:
        print("[safety] no key left held")


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    wav = os.path.abspath(sys.argv[sys.argv.index("--wav") + 1]) if "--wav" in sys.argv else \
        os.path.abspath(DEFAULT_WAV)
    timeout = float(sys.argv[sys.argv.index("--timeout") + 1]) if "--timeout" in sys.argv else 20.0
    stop_op = int(sys.argv[sys.argv.index("--stop-op") + 1], 0) if "--stop-op" in sys.argv else None
    log_path = os.path.join(runtime, "pipe_client_server.log")

    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    sp = SettingsPipe()
    print(f"[arm] {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()
    hwnd = make_foreground_window()
    print(f"[host] hwnd=0x{hwnd:X} pid={os.getpid()}")
    activate_doubao_for_process(verbose=False)

    pipe = Pipe(timeout=35.0)
    my_pid = os.getpid()
    for op, body, name in (
            (0x02, b"", "Activate"),
            (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb"), "ImeChanged"),
            (0x1B, bytes.fromhex("0801"), "SetUIElementShowState"),
            (0x0C, bytes.fromhex("08f8071801"), "SimpleMessage(1016,1)"),
            (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
             "FocusIn(pid)"),
            (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
             "RegisterTsfNotifySink"),
            (0x08, pb_int(1, 100) + pb_int(2, 100) + pb_int(3, 20), "SetCursorPos"),
            (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
             + lp("planb") + lp("python.exe"), "UpdateHostContext")):
        res = pipe.call(op, body)
        print(f"[ctx] {name:22s} -> {res if res is None else res[2]}")

    player = subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", wav],
                              creationflags=0x00000008) if os.path.exists(FFPLAY) else None
    time.sleep(0.3)
    send_key(0xA5)
    started = time.time()
    print("[key] Right Alt down (session start)")

    def log_text() -> str:
        try:
            return open(log_path, "rb").read().decode("utf-8", "replace")
        except OSError:
            return ""

    interim_seen = None
    stopped = False
    deadline = time.time() + timeout
    while time.time() < deadline and not stopped:
        time.sleep(0.2)
        blob = log_text()
        items = re.findall(r'"is_interim":(\w+),"text":"([^"]*)"', blob)
        if items and any(t for _, t in items):
            interim_seen = time.time() - started
            print(f"[asr] first non-empty result at t+{interim_seen:.1f}s")
            if "--poll-during" in sys.argv:
                print("[poll] while still recording:")
                for _ in range(20):
                    for op in (0x19, 0x09, 0x0A, 0x17):
                        res = pipe.call(op, b"")
                        if res and res[1] and res[1] != b"\x00" * len(res[1]):
                            print(f"   [op 0x{op:02X}] {res[1].hex()} "
                                  f"{res[1].decode('utf-8', 'replace')[:80]!r}")
                    time.sleep(0.2)
                print("[poll] done")
            if stop_op is None:
                send_click()
                print("[mouse] click sent to finalize in place")
            else:
                res = pipe.call(stop_op, b"")
                print(f"[rpc] stop op 0x{stop_op:02X} -> {res if res is None else res[2]}")
            time.sleep(0.4)
            blob = log_text()
            stopped = any(k in blob for k in ("click-finalize", "STOP_FAST", "PRESS_STOP",
                                              "voice stop", "VoiceStop"))
            if stopped:
                print("[engine] stop accepted")
            else:
                print("[engine] click not accepted yet - retrying")
    if not stopped:
        print("[warn] no stop accepted within the timeout; clicking once more")
        send_click()
        time.sleep(0.5)

    if player:
        try:
            player.kill()
        except Exception:
            pass
    send_key(0xA5, up=True)

    print("--- polling PeekVoiceCommit / GetInlineCommitText ---")
    deadline = time.time() + 12
    while time.time() < deadline:
        for op in (0x17, 0x19, 0x0A):
            res = pipe.call(op, b"\x00\x00\x00\x00")
            if res and res[1] and res[1] != b"\x00" * len(res[1]):
                print(f"   [op 0x{op:02X}] {res[1].hex()} "
                      f"{res[1].decode('utf-8', 'replace')[:100]!r}")
        time.sleep(0.5)

    blob = log_text()
    print("--- engine log highlights ---")
    for pattern in ("voice startfrom", "click-finalize", "STOP_FAST", "had_result_text=1",
                    "slot_PeekVoiceCommit session=0 bytes=0"):
        print(f"   {pattern}: {blob.count(pattern)}")
    items = re.findall(r'"is_interim":(\w+),"text":"([^"]*)"', blob)
    print(f"   asr results: {len(items)} (non-empty {sum(1 for _, t in items if t)})")
    pipe.close()
    proc.terminate()
    release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
