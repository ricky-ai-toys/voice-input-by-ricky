"""Plan B: let the vendor's own client library show us the wire format.

The `RpcPipe_*` exports of rpc.dll are thin wrappers around the private client object. By
hooking the single function that frames every request (rpc.dll+0x16650) and then calling an
export, we learn the exact op and protobuf body that op expects - no guessing.

    python oracle_client.py <runtime_dir> [export_name ...]

Each call runs in its own throwaway process: a wrong argument count can crash the caller,
which is fine for an oracle but must not take the harness down with it.
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
  const bytes = new Uint8Array(p.readByteArray(n));
  return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}
Interceptor.attach(rpcMod.base.add(0x16650), {
  onEnter(args) {
    const op = this.context.rdx.toInt32() & 0xffff;
    const line = { kind: 'send', op: op };
    try {
      const sp = this.context.r8;                       // std::string* payload
      const size = sp.add(0x10).readU64().toNumber();
      const cap = sp.add(0x18).readU64().toNumber();
      const data = cap >= 0x10 ? sp.readPointer() : sp;
      line.len = size;
      line.body = size > 0 && size < 512 ? hex(data, size) : '';
      try { line.text = data.readUtf8String(Math.min(size, 64)); } catch (e) {}
    } catch (e) {
      line.error = String(e);
    }
    send(line);
  },
});
send({ kind: 'ready' });
"""


def run_one(runtime: str, export: str, args: list[str]) -> int:
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))
    fn = getattr(dll, export)
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_char_p] * len(args)

    session = frida.attach(os.getpid())
    script = session.create_script(JS)
    seen: list[str] = []
    ready = {"ok": False}

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "ready":
            ready["ok"] = True
        elif payload.get("kind") == "send":
            seen.append(payload)

    script.on("message", on_message)
    script.load()
    while not ready["ok"]:
        time.sleep(0.05)

    def conv(value: str):
        if value in ("NULL", "null", "0x0"):
            return None
        if value.startswith("0x"):
            return int(value, 16)
        return value.encode("utf-8")

    print(f"[call] {export}({', '.join(repr(a) for a in args)})", flush=True)
    try:
        result = fn(*[conv(a) for a in args])
        print(f"[ret ] {result}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[exc ] {exc}", flush=True)
    time.sleep(0.5)
    for entry in seen:
        print(f"   op=0x{entry['op']:02X} len={entry.get('len')} body={entry.get('body')} "
              f"text={entry.get('text')!r}", flush=True)
    if not seen:
        print("   (no request was framed)", flush=True)
    return 0


DEFAULT_CALLS = {
    "RpcPipe_UpdateHostContextUtf8": [PIPE, "1234", "python.exe", "Chrome_WidgetWin_1",
                                      "Notepad", "1"],
    "RpcPipe_FocusIn": [PIPE],
    "RpcPipe_FocusOut": [PIPE],
    "RpcPipe_KeyDown": [PIPE, "165", "python.exe", "0"],
    "RpcPipe_KeyUp": [PIPE, "165", "python.exe", "0"],
    "RpcPipe_Activate": [PIPE],
    "RpcPipe_DeActivate": [PIPE],
    "RpcPipe_PeekVoiceCommitUtf8": [PIPE],
    "RpcPipe_AckVoiceCommit": [PIPE, "1"],
    "RpcPipe_GetInputState": [PIPE],
}


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    export = sys.argv[2] if len(sys.argv) > 2 else "RpcPipe_UpdateHostContextUtf8"
    args = sys.argv[3:] if len(sys.argv) > 3 else DEFAULT_CALLS.get(export, [PIPE])
    return run_one(runtime, export, args)


if __name__ == "__main__":
    sys.exit(main())
