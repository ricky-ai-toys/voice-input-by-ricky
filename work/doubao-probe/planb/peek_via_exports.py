"""Plan B: fetch the transcript with the vendor's own client API instead of hand-made frames.

Our raw pipe client got `session=0 bytes=0` for every `PeekVoiceCommit` because an *empty*
body fails the handler's message-schema check (`rpc.dll+0x24F4D` first calls the
`CheckTag(..., "PeekVoiceCommit")` helper and bails out when it fails). The exported
`RpcPipe_PeekVoiceCommitUtf8` builds a well-formed request, so this tool loads our patched
rpc.dll in-process, points it at the private pipe and polls it during a real voice session.

    python peek_via_exports.py scratch [--hold 8] [--tail 20]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

from pipe_client import Pipe, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window
from tsf_activate import activate_doubao_for_process

PIPE = "\\\\.\\pipe\\ObricIme\\oime-serve1"


def load_client(runtime: str):
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


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 8.0
    tail = float(sys.argv[sys.argv.index("--tail") + 1]) if "--tail" in sys.argv else 20.0

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
            # the vendor's own core sends this right after FocusIn; it is what makes the
            # engine report an editable host context (has_ctx=1)
            (0x0C, bytes.fromhex("08f8071801"), "SimpleMessage(1016,1)"),
            (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
             "FocusIn(pid)"),
            (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
             "RegisterTsfNotifySink"),
            # a *non-empty* host text context: the engine only caches a context when the
            # caller reports text around the caret (shell_impl "has_context")
            (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
             + lp("planb") + lp("python.exe"), "UpdateHostContext(text)")):
        res = pipe.call(op, body)
        print(f"[ctx] {name:22s} -> {res if res is None else res[2]}")

    peek, ack = load_client(runtime)
    buf = ctypes.create_string_buffer(0x40001)
    session = ctypes.c_uint64(0)

    def poll(label: str) -> None:
        buf.value = b""
        session.value = 0
        count = peek(PIPE.encode(), ctypes.byref(session), buf, 0x40001)
        text = buf.value.decode("utf-8", "replace")
        if count or session.value or text:
            print(f"   [export peek {label}] ret={count} session={session.value} text={text!r}")
            if text:
                print(f"   [export ack ] {ack(PIPE.encode(), session.value)}")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
    user32.keybd_event(0xA5, 0, 0, None)
    print("[key] Right Alt down")
    deadline = time.time() + hold
    while time.time() < deadline:
        time.sleep(0.3)
        poll("hold")
    user32.keybd_event(0xA5, 0, 2, None)
    print("[key] Right Alt up")
    deadline = time.time() + tail
    while time.time() < deadline:
        time.sleep(0.4)
        poll("tail")

    pipe.close()
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
