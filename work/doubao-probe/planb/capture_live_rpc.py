"""Plan B: capture real rpc.dll calls from a live app that has the IME loaded.

The vendor's own client (tsf-oime-core.dll inside every app) is the ground truth for the
RPC API. Attaching here and logging the arguments of every RpcPipe_* call tells us the real
signatures and the real call order - no guessing.

    python capture_live_rpc.py [seconds] [process-name-filter]
"""
import os
import sys
import time

import frida

JS = r"""
const mod = Process.findModuleByName('rpc.dll');
if (!mod) {
  send({ kind: 'error', message: 'rpc.dll not loaded in this process' });
} else {
  const exports = mod.enumerateExports().filter(e => e.name.startsWith('RpcPipe_') || e.name.startsWith('CreateRpc') || e.name.startsWith('DestroyRpc'));
  send({ kind: 'info', message: 'hooking ' + exports.length + ' exports in ' + Process.enumerateModules()[0].name });
  for (const exp of exports) {
    try {
      Interceptor.attach(exp.address, {
        onEnter(args) {
          const ctx = this.context;
          const info = { kind: 'call', fn: exp.name, rcx: ctx.rcx.toString(), rdx: ctx.rdx.toString(),
                         r8: ctx.r8.toString(), r9: ctx.r9.toString() };
          for (const [reg, val] of [['rcx', ctx.rcx], ['rdx', ctx.rdx], ['r8', ctx.r8], ['r9', ctx.r9]]) {
            try {
              const s = val.readUtf8String(64);
              if (s && /^[\x20-\x7e]{2,}$/.test(s)) info[reg + '_str'] = s;
            } catch (e) {}
            try {
              const w = val.readUtf16String(64);
              if (w && /^[\x20-\uffff]{2,}$/.test(w) && !info[reg + '_str']) info[reg + '_wstr'] = w;
            } catch (e) {}
          }
          send(info);
        }
      });
    } catch (e) {}
  }
}
"""


def pick_pid(name_filter: str | None) -> int | None:
    for proc in frida.get_local_device().enumerate_processes():
        if name_filter and name_filter.lower() not in proc.name.lower():
            continue
        try:
            session = frida.attach(proc.pid)
        except Exception:
            continue
        try:
            script = session.create_script(
                "send({kind:'probe', loaded: !!Process.findModuleByName('rpc.dll')});"
            )
            result = {}
            script.on("message", lambda m, d: result.update(m.get("payload") or {}))
            script.load()
            if result.get("loaded"):
                session.detach()
                return proc.pid
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
    return None


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
    name_filter = sys.argv[2] if len(sys.argv) > 2 else None
    pid = pick_pid(name_filter)
    if not pid:
        print("[fail] no process with rpc.dll loaded found")
        return 1
    print(f"[info] attaching to pid {pid}")

    session = frida.attach(pid)
    script = session.create_script(JS)
    calls: list[str] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        kind = payload.get("kind")
        if kind == "info":
            print(f"[info] {payload.get('message')}", flush=True)
        elif kind == "error":
            print(f"[error] {payload.get('message')}", flush=True)
        elif kind == "call":
            detail = " ".join(f"{k}={v}" for k, v in payload.items() if k not in ("kind", "fn"))
            calls.append(payload["fn"])
            print(f"[call] {payload['fn']}  {detail}", flush=True)

    script.on("message", on_message)
    script.load()
    print(f"[info] capturing for {seconds:.0f}s - switch focus between windows to trigger IME RPCs")
    time.sleep(seconds)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    print(f"[summary] {len(calls)} call(s): " + ", ".join(f"{k}x{v}" for k, v in
          {n: calls.count(n) for n in dict.fromkeys(calls)}.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
