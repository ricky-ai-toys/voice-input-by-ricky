"""Plan B: activate the Doubao input profile inside another process and watch its pipes.

    python activate_profile_inject.py <pid|name-substring> [seconds]

Attaches with frida, runs activate_profile_inject.js (process-wide profile activation) and
then logs the target's engine-pipe traffic, so a real keystroke (press Right Alt while the
target window has focus) reveals the voice op codes.
"""
import os
import sys
import time

import frida

PIPE_JS = r"""
const k32 = Process.getModuleByName('kernel32.dll');
const pipes = {};
for (const api of ['CreateFileW', 'CreateFileA']) {
  const a = k32.getExportByName(api);
  if (!a) continue;
  Interceptor.attach(a, {
    onEnter(args) { try { this.n = api.endsWith('W') ? args[0].readUtf16String() : args[0].readAnsiString(); } catch (e) { this.n = ''; } },
    onLeave(r) { const n = this.n || ''; if (n.toLowerCase().indexOf('pipe') >= 0) { pipes[r.toString()] = n; send({kind:'open', n:n}); } }
  });
}
const w = k32.getExportByName('WriteFile');
Interceptor.attach(w, {
  onEnter(args) {
    const n = pipes[args[0].toString()];
    if (!n) return;
    try {
      const len = args[2].toInt32();
      if (len <= 0 || len > 65536) return;
      const b = new Uint8Array(args[1].readByteArray(Math.min(len, 256)));
      let hex = ''; for (let i = 0; i < b.length; i++) hex += b[i].toString(16).padStart(2, '0');
      send({kind:'write', n:n, len:len, hex:hex});
    } catch (e) {}
  }
});
send({kind:'ready'});
"""


def main() -> int:
    target = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
    pid = int(target) if target.isdigit() else None
    if pid is None:
        for proc in frida.get_local_device().enumerate_processes():
            if target.lower() in proc.name.lower():
                pid = proc.pid
                print(f"[info] matched {proc.name} pid={pid}")
                break
    if pid is None:
        print("[fail] process not found")
        return 1

    session = frida.attach(pid)
    here = os.path.dirname(os.path.abspath(__file__))
    activate = open(os.path.join(here, "activate_profile_inject.js"), encoding="utf-8").read()
    pipe_js = PIPE_JS

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        p = message.get("payload") or {}
        k = p.get("kind")
        if k == "cocreate":
            print(f"[activate] CoCreateInstance hr=0x{p['hr'] & 0xFFFFFFFF:08X} ptr={p['ptr']}", flush=True)
        elif k == "activate":
            print(f"[activate] ActivateProfile hr=0x{p['hr'] & 0xFFFFFFFF:08X}", flush=True)
        elif k == "error":
            print(f"[error] {p.get('message')}", flush=True)
        elif k == "ready":
            print("[info] pipe hooks ready - now press Right Alt in that window", flush=True)
        elif k == "open":
            print(f"[open ] {p['n']}", flush=True)
        elif k == "write":
            raw = bytes.fromhex(p["hex"])
            text = "".join(chr(b) if 32 <= b < 127 else "." for b in raw[:100])
            print(f"[write] {p['n']} len={p['len']} head={p['hex'][:64]}\n        {text}", flush=True)

    script = session.create_script(activate + "\n" + pipe_js)
    script.on("message", on_message)
    script.load()
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
