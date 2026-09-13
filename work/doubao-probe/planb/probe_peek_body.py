"""Plan B: what does a *well-formed* PeekVoiceCommit request look like?

Our hand-made op-0x17 frames come back as 12 zero bytes and never reach the engine-side
implementation, so the body must be wrong. This tool calls the vendor's own
`RpcPipe_PeekVoiceCommitUtf8` with proper arguments and hooks the single framing function
(`rpc.dll+0x16650`) to capture the exact request it builds.

    python probe_peek_body.py scratch
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

import frida

PIPE = "\\\\.\\pipe\\ObricIme\\oime-serve1"

JS = r"""
const rpcMod = Process.getModuleByName('rpc.dll');
function hex(p, n) {
  const b = new Uint8Array(p.readByteArray(n));
  return Array.from(b).map(x => x.toString(16).padStart(2, '0')).join('');
}
Interceptor.attach(rpcMod.base.add(0x16650), {
  onEnter(args) {
    const op = this.context.rdx.toInt32() & 0xffff;
    const line = { kind: 'send', op: op };
    try {
      const sp = this.context.r8;
      const size = sp.add(0x10).readU64().toNumber();
      const cap = sp.add(0x18).readU64().toNumber();
      const data = cap >= 0x10 ? sp.readPointer() : sp;
      line.len = size;
      if (size > 0 && size < 256) line.body = hex(data, size);
    } catch (e) { line.error = String(e); }
    send(line);
  }
});
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))

    peek = dll.RpcPipe_PeekVoiceCommitUtf8
    peek.restype = ctypes.c_int
    peek.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_char_p,
                     ctypes.c_int]
    ack = dll.RpcPipe_AckVoiceCommit
    ack.restype = ctypes.c_int
    ack.argtypes = [ctypes.c_char_p, ctypes.c_uint64]

    session = frida.attach(os.getpid())
    script = session.create_script(JS)
    seen: list[dict] = []
    ready = {"ok": False}

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "ready":
            ready["ok"] = True
        else:
            seen.append(payload)

    script.on("message", on_message)
    script.load()
    while not ready["ok"]:
        time.sleep(0.05)

    buf = ctypes.create_string_buffer(0x40001)
    sess = ctypes.c_uint64(0)
    ret = peek(PIPE.encode(), ctypes.byref(sess), buf, 0x40001)
    print(f"[peek] ret={ret} session={sess.value} text={buf.value!r}")
    ret_ack = ack(PIPE.encode(), sess.value or 1)
    print(f"[ack ] ret={ret_ack}")
    time.sleep(0.5)
    for entry in seen:
        print(f"   op=0x{entry['op']:02X} len={entry.get('len')} body={entry.get('body')}")
    if not seen:
        print("   (nothing framed - is the engine running on the private pipe?)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
