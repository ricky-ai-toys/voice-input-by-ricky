"""Plan B: start the private engine, hook its dispatcher, then drive it from the pipe.

Single-process version of hook_impl.py + pipe_client.py, so the frida session is attached to
exactly the engine instance our frames reach.

    python hook_drive.py scratch
"""
from __future__ import annotations

import os
import sys
import time

import frida

from pipe_client import Pipe, log_grep, pb_int, pb_str, start_server

JS = r"""
function modOf(addr) {
  const m = Process.findModuleByAddress(addr);
  return m ? (m.name + '+0x' + addr.sub(m.base).toString(16)) : addr.toString();
}
const rpcMod = Process.getModuleByName('rpc.dll');
const exeMod = Process.getModuleByName('ImeService.exe');
send({ kind: 'info', message: 'rpc.dll base ' + rpcMod.base + ', ImeService.exe base ' + exeMod.base });

Interceptor.attach(rpcMod.base.add(0x23730), {
  onEnter(args) {
    const server = this.context.rcx;
    const op = this.context.rdx.toInt32() & 0xffff;
    const line = { kind: 'call', op: op };
    try {
      const body = this.context.r8;
      const len = this.context.r9;
      const n = len.toInt32();
      if (n > 0 && n < 512) {
        line.body = Array.from(new Uint8Array(body.readByteArray(n))).map(b => b.toString(16).padStart(2, '0')).join('');
      }
    } catch (e) { line.body_err = String(e); }
    try {
      // rpc::PipeRpcServer::Dispatch loads r13 = this and r14 = this->[0x30] (the service
      // implementation object); the handler then calls [*(r14) + 8*op].
      const impl = server.add(0x30).readPointer();
      const vt = impl.readPointer();
      line.impl = impl.toString();
      line.vtable = vt.toString();
      if (op > 0 && op <= 0x1c) {
        line.target = modOf(vt.add(op * 8).readPointer());
      }
    } catch (e) {
      line.error = String(e);
    }
    send(line);
  },
});

// also dump the arguments the engine-side implementations receive
for (const [off, name] of [[0x80f620, 'slot_FocusIn'], [0x80fb30, 'slot_KeyDown'],
                           [0x80fd70, 'slot_KeyUp']]) {
  try {
    Interceptor.attach(exeMod.base.add(off), {
      onEnter(args) {
        const info = { kind: 'impl', name: name, rcx: this.context.rcx.toString(),
                       rdx: this.context.rdx.toInt32(), r8: this.context.r8.toString(),
                       r9: this.context.r9.toString() };
        try {
          const s = this.context.r8.readUtf8String(64);
          if (s) info.r8_str = s;
        } catch (e) {}
        send(info);
      },
    });
  } catch (e) {
    send({ kind: 'warn', message: name + ' hook failed: ' + e });
  }
}
send({ kind: 'info', message: 'dispatcher hooked' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    try:
        pipe = Pipe(timeout=35.0)
    except OSError as exc:
        print(f"[fail] {exc}")
        log_grep(runtime, 15)
        return 1
    print("[ok] pipe connected")

    session = frida.attach(proc.pid)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print(f"   [hook] {m.get('payload')}", flush=True))
    script.load()
    time.sleep(1.0)

    host = os.path.basename(sys.executable or "python.exe")
    ctx = bytes.fromhex("08860000010000000a000000707974686f6e2e657865")
    for op, body, label in ((0x01, b"", "SendHeart"), (0x06, ctx, "FocusIn(host ctx)")):
        res = pipe.call(op, body)
        print(f"[{label}] {res[2] if res else 'none'}")
    key = 0xA5
    res = pipe.call(0x04, pb_int(1, key) + pb_str(3, host))
    print(f"[KeyDown 0xA5] {res}")
    for i in range(3):
        time.sleep(1.0)
        res = pipe.call(0x17, b"")
        print(f"[peek {i}] {res}")
    time.sleep(0.5)
    res = pipe.call(0x05, pb_int(1, key) + pb_str(3, host))
    print(f"[KeyUp 0xA5] {res}")
    time.sleep(0.5)
    pipe.close()

    try:
        script.unload()
    except Exception:
        pass
    log_grep(runtime, 15)
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
