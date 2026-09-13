"""Plan B: does the engine-side PeekVoiceCommit implementation ever run?

Spawns the private engine with a frida hook on `ImeService.exe+0x80F530` (the vtable entry for
op 0x17) and then calls the vendor's own `RpcPipe_PeekVoiceCommitUtf8`, which is known to send a
well-formed request. If the hook fires, our hand-made frame is the problem; if it does not, the
text takes a different route and 0x80F530 is not the text getter.

    python probe_peek_impl.py scratch
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

import frida

PIPE = "\\\\.\\pipe\\ObricIme\\oime-serve1"

JS = r"""
const exe = Process.getModuleByName('ImeService.exe');
// the function that logs "slot_PeekVoiceCommit session=%d bytes=%d acked=%d" (controller.cpp)
const slot = exe.base.add(0x7982d0);
Interceptor.attach(slot, {
  onEnter(args) { send({ kind: 'impl', name: 'slot_PeekVoiceCommit', a0: args[0].toString(), a1: args[1].toString(), a2: args[2].toString() }); }
});
// ...and the commit-text field of the controller object (rdi+0x9f0 string, +0xa80 session)
send({ kind: 'ready' });
"""

JS_OLD = r"""
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    exe = os.path.join(runtime, "ImeService.exe")
    device = frida.get_local_device()
    log = open(os.path.join(runtime, "probe_peek_impl_server.log"), "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session = frida.attach(pid)
    script = session.create_script(JS)
    fired: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "impl":
            fired.append(payload)
            print(f"   [impl] op=0x{payload['op']:02X} a1={payload['a1']} a2={payload['a2']}")
        else:
            print(f"   [hook] {payload}")

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    time.sleep(3.0)

    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))
    peek = dll.RpcPipe_PeekVoiceCommitUtf8
    peek.restype = ctypes.c_int
    peek.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_char_p,
                     ctypes.c_int]
    buf = ctypes.create_string_buffer(0x40001)
    sess = ctypes.c_uint64(0)
    ret = peek(PIPE.encode(), ctypes.byref(sess), buf, 0x40001)
    print(f"[peek] ret={ret} session={sess.value} text={buf.value!r}")
    time.sleep(1.0)
    print(f"[done] impl fired {len(fired)} time(s)")
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
