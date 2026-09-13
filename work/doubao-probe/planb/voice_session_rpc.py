"""Plan B: start/stop the voice session over RPC instead of injecting keys.

The vendor's own client (the TSF core) reports key presses to the engine with op 0x04/0x05
(`KeyCode`). Those two ops are the natural product path: no keyboard injection, no dependency
on the engine's low-level hook, and - crucially - a *clean stop* so the engine can finalize and
commit the transcript.

    python voice_session_rpc.py scratch [--hold 8] [--tail 20] [--key 165]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from pipe_client import Pipe, pb_int, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window
from tsf_activate import activate_doubao_for_process

FFPLAY = r"E:\ffmpeg\bin\ffplay.exe"
DEFAULT_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "audio", "three_zh_16k.wav")


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 8.0
    tail = float(sys.argv[sys.argv.index("--tail") + 1]) if "--tail" in sys.argv else 20.0
    key = int(sys.argv[sys.argv.index("--key") + 1], 0) if "--key" in sys.argv else 165
    wav = os.path.abspath(sys.argv[sys.argv.index("--wav") + 1]) if "--wav" in sys.argv else \
        os.path.abspath(DEFAULT_WAV)
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
    for op, body, name in (
            (0x02, b"", "Activate"),
            (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb"), "ImeChanged"),
            (0x1B, bytes.fromhex("0801"), "SetUIElementShowState"),
            (0x0C, bytes.fromhex("08f8071801"), "SimpleMessage(1016,1)"),
            (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
             "FocusIn(pid)"),
            (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
             "RegisterTsfNotifySink"),
            (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
             + lp("planb") + lp("python.exe"), "UpdateHostContext(text)")):
        res = pipe.call(op, body)
        print(f"[ctx] {name:22s} -> {res if res is None else res[2]}")

    player = None
    if os.path.exists(FFPLAY) and os.path.exists(wav):
        player = subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", wav],
                                  creationflags=0x00000008)
    time.sleep(0.4)

    keycode = pb_int(1, key) + pb_str(3, os.path.basename(sys.executable or "python.exe"))
    start = time.time()
    res = pipe.call(0x04, keycode)
    print(f"[rpc] KeyDown 0x{key:02X} -> {res if res is None else res[2]}")

    def poll(label: str) -> None:
        res = pipe.call(0x17, b"\x00\x00\x00\x00")
        if res and res[1] and res[1] != b"\x00" * len(res[1]):
            print(f"   [peek {label}] {res[1].hex()} "
                  f"{res[1].decode('utf-8', 'replace')[:80]!r}")

    deadline = time.time() + hold
    while time.time() < deadline:
        time.sleep(0.5)
        poll("hold")
    res = pipe.call(0x05, keycode)
    print(f"[rpc] KeyUp   0x{key:02X} -> {res if res is None else res[2]} "
          f"(held {time.time() - start:.1f}s)")
    if player:
        try:
            player.kill()
        except Exception:
            pass

    deadline = time.time() + tail
    while time.time() < deadline:
        time.sleep(0.5)
        poll("tail")

    pipe.close()
    print("--- engine log (voice session) ---")
    if os.path.exists(log_path):
        import re
        noise = ("slot_PeekVoiceCommit", "sami feed chunk", "transport_parameters", "ttnet",
                 "Cronet", "ime_net_sdk")
        pat = re.compile(r"voice startfrom|voice stop|VoiceStop|StopVoice|commit|restart|"
                         r"voice record start|asr session started|voice-amp")
        text = open(log_path, encoding="utf-8", errors="replace").read().splitlines()
        for line in [l.strip() for l in text if l.strip() and pat.search(l)
                     and not any(n in l for n in noise)][-18:]:
            print("   " + line[:180])
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
