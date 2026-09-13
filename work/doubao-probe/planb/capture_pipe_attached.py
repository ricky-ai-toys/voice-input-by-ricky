"""Plan B: attach to an existing app that has the IME loaded and log its pipe traffic.

Unlike run_with_hooks.py (which spawns a process) this attaches to a running one, so we can
watch what the vendor client sends when the user really triggers voice input.

    python capture_pipe_attached.py <pid|name-substring> <seconds>
"""
import sys
import time

import frida

JS = r"""
const k32 = Process.getModuleByName('kernel32.dll');
const pipes = {};
for (const api of ['CreateFileW', 'CreateFileA']) {
  const addr = k32.getExportByName(api);
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      try { this.pipeName = api === 'CreateFileW' ? args[0].readUtf16String() : args[0].readAnsiString(); }
      catch (e) { this.pipeName = ''; }
    },
    onLeave(retval) {
      const n = this.pipeName || '';
      if (n.toLowerCase().indexOf('pipe') >= 0) { pipes[retval.toString()] = n; send({kind:'open', name:n}); }
    }
  });
}
for (const api of ['WriteFile']) {
  const addr = k32.getExportByName(api);
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      const name = pipes[args[0].toString()];
      if (!name) return;
      try {
        const len = args[2].toInt32();
        if (len <= 0 || len > 65536) return;
        const bytes = new Uint8Array(args[1].readByteArray(Math.min(len, 512)));
        let hex = '';
        for (let i = 0; i < bytes.length; i++) hex += bytes[i].toString(16).padStart(2, '0');
        send({kind:'write', name:name, len:len, hex:hex});
      } catch (e) {}
    }
  });
}
send({kind:'ready', pid: Process.id});
"""


def main() -> int:
    target = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0

    pid = None
    if target.isdigit():
        pid = int(target)
    else:
        for proc in frida.get_local_device().enumerate_processes():
            if target.lower() in proc.name.lower():
                pid = proc.pid
                print(f"[info] matched {proc.name} pid={pid}")
                break
    if pid is None:
        print("[fail] process not found")
        return 1

    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        p = message.get("payload") or {}
        if p.get("kind") == "open":
            print(f"[open ] {p['name']}", flush=True)
        elif p.get("kind") == "write":
            raw = bytes.fromhex(p["hex"])
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in raw[:120])
            print(f"[write] {p['name']} len={p['len']} head={p['hex'][:64]}\n        {text}", flush=True)
        elif p.get("kind") == "ready":
            print(f"[info] hooks ready in pid {p['pid']}", flush=True)

    script.on("message", on_message)
    script.load()
    print(f"[info] capturing {seconds:.0f}s ...")
    time.sleep(seconds)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
