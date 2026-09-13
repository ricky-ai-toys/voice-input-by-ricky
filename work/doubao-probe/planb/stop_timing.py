"""Plan B: how long does the engine's keyboard hook keep seeing our synthetic keys?

The session starts from an injected key-down, but the injected keys seem to stop arriving after
a few seconds of recording - which is why the stop (`PRESS_STOP`, triggered by an unrelated key)
only worked in short runs. This tool measures the window, so the stop can be scheduled inside it.

    python stop_timing.py scratch
"""
from __future__ import annotations

import os
import re
import sys
import time

from pipe_client import Pipe, pb_int, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_key
from tsf_activate import activate_doubao_for_process


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    log_path = os.path.join(runtime, "pipe_client_server.log")
    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    sp = SettingsPipe()
    sp.call(request("settings.setVoiceTryoutActive", {"active": True, "cookie": int(time.time())}))
    sp.close()
    hwnd = make_foreground_window()
    print(f"[host] hwnd=0x{hwnd:X}")
    activate_doubao_for_process(verbose=False)
    pipe = Pipe(timeout=35.0)
    my_pid = os.getpid()
    for op, body in ((0x02, b""),
                     (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb")),
                     (0x1B, bytes.fromhex("0801")),
                     (0x0C, bytes.fromhex("08f8071801")),
                     (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe")),
                     (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little")),
                     (0x08, pb_int(1, 100) + pb_int(2, 100) + pb_int(3, 20)),
                     (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
                      + lp("planb") + lp("python.exe"))):
        pipe.call(op, body)

    start = time.time()
    send_key(0xA5)
    print("[key] Right Alt down (session start)")
    marks: list[str] = []
    for target in (2, 3, 4, 5, 6, 7, 8, 9):
        while time.time() - start < target:
            time.sleep(0.05)
        send_key(0x41)
        time.sleep(0.05)
        send_key(0x41, up=True)
        time.sleep(0.35)
        text = open(log_path, "rb").read().decode("utf-8", "replace")
        seen = f"0x41(65) down=1" in text
        stopped = "controller handle msg=PRESS_STOP" in text
        marks.append(f"t+{target}s seen={seen} stop={stopped}")
        print(f"   t+{target}s  key seen={seen}  PRESS_STOP={stopped}")
        if stopped:
            break
    send_key(0xA5, up=True)
    print("[key] Right Alt up")
    print(f"[result] {marks}")
    pipe.close()
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
