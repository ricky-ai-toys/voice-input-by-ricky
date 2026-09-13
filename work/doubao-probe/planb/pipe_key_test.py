"""Can the vendor's own RpcPipe_KeyDown/KeyUp drive a voice session instead of the hotkey?

If yes, the acceptance test needs no frida and no injected keys: the client simply tells the
engine "Right Alt went down" over the same pipe the TSF core would use.

    python pipe_key_test.py scratch
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

from pipe_client import pb_int, pb_str
from planb_voice_input import FFPLAY, PIPE_NAME, PlanBVoice

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audio", "three_zh_16k.wav")
VK_RMENU = 0xA5


def load_keys(runtime: str):
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))
    down = dll.RpcPipe_KeyDown
    up = dll.RpcPipe_KeyUp
    for fn in (down, up):
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    return down, up


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "scratch")
    voice = PlanBVoice(runtime, paste=False, use_tsf=False)
    voice.arm()
    down, up = load_keys(runtime)
    pipe = PIPE_NAME.encode()

    player = subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", WAV],
                              creationflags=0x00000008) if os.path.exists(WAV) else None
    time.sleep(0.3)
    voice.on_press()
    body = pb_int(1, VK_RMENU) + pb_str(3, "python.exe")
    print(f"[raw 0x04] {voice.pipe.call(0x04, body)}", flush=True)
    print(f"[export KeyDown] -> {down(pipe, VK_RMENU, b'python.exe', 0)}", flush=True)
    time.sleep(6.0)
    print(f"[raw 0x05] {voice.pipe.call(0x05, body)}", flush=True)
    print(f"[export KeyUp  ] -> {up(pipe, VK_RMENU, b'python.exe', 0)}", flush=True)
    voice.on_release()
    if player:
        try:
            player.kill()
        except Exception:
            pass
    print(f"[session] {voice.text!r}")
    voice.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
