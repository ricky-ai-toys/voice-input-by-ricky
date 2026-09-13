"""Plan B: run probe_client_connect.py under frida while logging pipe opens.

    python run_with_hooks.py <runtime_dir>

The hook answers the open question from the last session: when the client API is used, which
pipe path does it actually open (our private one, the official one, or none)?
"""
import os
import subprocess
import sys
import time

import frida

JS = r"""
const k32 = Process.getModuleByName('kernel32.dll');
const pipes = {};          // handle value -> pipe name

for (const api of ['CreateFileW', 'CreateFileA']) {
  const addr = k32.getExportByName(api);
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      try {
        const s = api === 'CreateFileW' ? args[0].readUtf16String() : args[0].readAnsiString();
        this.pipeName = s;
      } catch (e) { /* ignore */ }
    },
    onLeave(retval) {
      const name = this.pipeName || '';
      if (name.toLowerCase().indexOf('pipe') >= 0) {
        pipes[retval.toString()] = name;
        send({ kind: 'open', api: 'CreateFile', name: name, handle: retval.toString() });
      }
    }
  });
}

// log writes/reads on the engine pipes -> that is the RPC traffic
for (const api of ['WriteFile', 'ReadFile']) {
  const addr = k32.getExportByName(api);
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      const h = args[0].toString();
      const name = pipes[h];
      if (!name) return;
      try {
        const len = args[2].toInt32();
        if (len <= 0 || len > 65536) return;
        const bytes = new Uint8Array(args[1].readByteArray(len));
        let hex = '';
        for (let i = 0; i < Math.min(bytes.length, 64); i++) {
          hex += bytes[i].toString(16).padStart(2, '0');
        }
        send({ kind: api.toLowerCase(), name: name, len: len, hex: hex });
      } catch (e) { /* ignore */ }
    }
  });
}
// low level: catches anything that bypasses kernel32
try {
  const ntdll = Process.getModuleByName('ntdll.dll');
  const ncf = ntdll.getExportByName('NtCreateFile');
  Interceptor.attach(ncf, {
    onEnter(args) {
      try {
        const oa = args[2];
        if (oa.isNull()) return;
        const namePtr = oa.add(16).readPointer();
        if (namePtr.isNull()) return;
        const len = namePtr.readU16();
        const buf = namePtr.add(8).readPointer();
        if (len > 0 && !buf.isNull()) send({ kind: 'open', api: 'NtCreateFile', name: buf.readUtf16String(len / 2) });
      } catch (e) { /* ignore */ }
    }
  });
} catch (e) { send({ kind: 'warn', message: 'ntdll hook failed: ' + e.message }); }
send({ kind: 'hooked' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    here = os.path.dirname(os.path.abspath(__file__))
    probe_name = sys.argv[2] if len(sys.argv) > 2 else "probe_client_connect.py"
    probe = os.path.join(here, probe_name)
    argv = [sys.executable, probe, runtime]

    pid = frida.spawn(sys.executable, argv=argv, cwd=os.path.dirname(probe), stdio="inherit")
    session = frida.attach(pid)
    script = session.create_script(JS)
    opens: list[str] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        if payload.get("kind") == "open":
            name = payload.get("name", "")
            opens.append(name)
            if "pipe" in name.lower() or "oime" in name.lower():
                print(f"[hook] {payload.get('api')}('{name}')", flush=True)
        elif payload.get("kind") in ("writefile", "readfile"):
            print(f"[{payload['kind']}] {payload['name']} len={payload['len']} "
                  f"head={payload['hex']}", flush=True)

    script.on("message", on_message)
    script.load()
    frida.resume(pid)

    deadline = time.time() + 30
    while time.time() < deadline:
        if str(pid) not in subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout:
            break
        time.sleep(0.3)

    print(f"[summary] pipe opens observed: {len(opens)}")
    for name in dict.fromkeys(opens):
        print(f"[summary]   {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
