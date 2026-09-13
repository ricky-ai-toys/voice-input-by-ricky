"""Plan B: watch which named pipes the engine creates (and why one is missing).

`ImeService.exe` logs `settings_ipc_server started on \\.\pipe\DoubaoIme\settings-rp1`, but the
pipe never shows up in the object namespace. Hooking `CreateNamedPipeW`/`ConnectNamedPipe`
inside a freshly spawned copy says exactly what happened.

    python hook_pipe_create.py scratch [seconds]
"""
from __future__ import annotations

import os
import sys
import time

import frida

JS = r"""
const k32 = Process.getModuleByName('kernel32.dll');
const create = k32.getExportByName('CreateNamedPipeW');
const connect = k32.getExportByName('ConnectNamedPipe');
const close = k32.getExportByName('CloseHandle');

Interceptor.attach(create, {
  onEnter(args) {
    try { this.name = args[0].readUtf16String(); } catch (e) { this.name = '?'; }
  },
  onLeave(retval) {
    send({ kind: 'create', name: this.name, handle: retval.toString(),
           gle: Process.getCurrentThreadId() ? 0 : 0 });
  }
});
Interceptor.attach(connect, {
  onEnter(args) { send({ kind: 'connect', handle: args[0].toString() }); },
  onLeave(retval) { send({ kind: 'connect_ret', retval: retval.toString() }); }
});
send({ kind: 'hooked' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    exe = os.path.join(runtime, "ImeService.exe")
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print(f"[hook] {m.get('payload')}", flush=True))
    script.load()
    frida.resume(pid)
    print(f"[info] engine pid={pid}")
    time.sleep(seconds)
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
