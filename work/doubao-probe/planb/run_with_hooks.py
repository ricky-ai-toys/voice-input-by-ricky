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
let seen = {};
const k32 = Process.getModuleByName('kernel32.dll');
for (const api of ['CreateFileW', 'CreateFileA']) {
  const addr = k32.getExportByName(api);
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      try {
        const s = api === 'CreateFileW' ? args[0].readUtf16String() : args[0].readAnsiString();
        if (s) send({ kind: 'open', api: api, name: s });
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
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_client_connect.py")
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
