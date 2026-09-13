"""Why the second voice session refuses to start, and what clears it.

The engine log says it plainly:

    [controller.cpp:3429] voice record start blocked by unacked commit session=1 bytes=60

After a session the engine holds the transcript as a "commit" until the client acks it. Our
hand-made op 0x18 never cleared it (the handler wants a well-formed body), but the vendor's own
export does, so this probe loads the copy's rpc.dll and uses

    RpcPipe_PeekVoiceCommitUtf8(pipe, &session, buf, size) -> length
    RpcPipe_AckVoiceCommit(pipe, session)                  -> ?

then runs a second session to prove the gate is gone.

    python ack_probe.py scratch
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

from pipe_client import pb_dump
from planb_voice_input import FFPLAY, PlanBVoice, relax_engine_filters, restore_engine_filters
from try_voice import send_key

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audio", "three_zh_16k.wav")
PIPE = "\\\\.\\pipe\\ObricIme\\oime-serve1"
VK_RMENU = 0xA5


def load_client(runtime: str):
    """The vendor's own commit API, from the copy we are allowed to instrument."""
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))
    peek = dll.RpcPipe_PeekVoiceCommitUtf8
    peek.restype = ctypes.c_int
    peek.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_char_p,
                     ctypes.c_int]
    ack = dll.RpcPipe_AckVoiceCommit
    ack.restype = ctypes.c_int
    ack.argtypes = [ctypes.c_char_p, ctypes.c_uint64]
    return peek, ack


def run_session(voice: PlanBVoice, hold: float = 6.0) -> str:
    player = None
    if os.path.exists(FFPLAY) and os.path.exists(WAV):
        player = subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", WAV],
                                  creationflags=0x00000008)
    time.sleep(0.3)
    voice.on_press()
    send_key(VK_RMENU)
    time.sleep(hold)
    send_key(VK_RMENU, up=True)
    voice.on_release()
    if player:
        try:
            player.kill()
        except Exception:
            pass
    return voice.text


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "scratch")
    voice = PlanBVoice(runtime, paste=False, use_tsf=False)
    voice.arm()                      # voice-tryout + host context; without it the hook refuses
    script, originals = relax_engine_filters(voice.proc.pid)
    try:
        print(f"[session 1] {run_session(voice)!r}")
        res = voice.pipe.call(0x17, b"")
        print(f"[raw 0x17] status={res[2] if res else '-'} len={len(res[1]) if res else 0} "
              f"{pb_dump(res[1]) if res else ''}")

        peek, ack = load_client(runtime)
        buf = ctypes.create_string_buffer(0x40001)
        session = ctypes.c_uint64(0)
        count = peek(PIPE.encode(), ctypes.byref(session), buf, 0x40001)
        text = buf.value.decode("utf-8", "replace")
        print(f"[export peek] ret={count} session={session.value} text={text!r}")
        print(f"[export ack ] -> {ack(PIPE.encode(), session.value)}")

        res = voice.pipe.call(0x17, b"")
        print(f"[raw 0x17 after ack] status={res[2] if res else '-'} "
              f"len={len(res[1]) if res else 0} {pb_dump(res[1]) if res else ''}")

        print(f"[session 2] {run_session(voice)!r}")
    finally:
        restore_engine_filters(script, originals)
        voice.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
