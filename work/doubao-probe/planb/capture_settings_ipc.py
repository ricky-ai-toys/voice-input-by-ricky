"""Plan B: capture the settings app's local RPC to the engine.

`ImeService.exe` hosts `SettingsIpcServer` (commands `settings.get`, `settings.update`,
`settings.setVoiceTryoutActive`, `settings.startMicMeter`, ...) on
`\\.\pipe\DoubaoIme\settings-rpc`, and the voice hotkey is gated by the "voice tryout" state
that this channel flips. Capturing one real request from `DoubaoImeSettings.exe` gives us the
framing so our own client can request the same state.

    python capture_settings_ipc.py [seconds]
"""
from __future__ import annotations

import sys
import time

import frida

EXE = r"C:\Program Files\DoubaoIME\versions\v0.9.0.0\DoubaoImeSettings.exe"

JS = r"""
const k32 = Process.getModuleByName('kernel32.dll');
const pipes = {};
for (const api of ['CreateFileW', 'CreateFileA']) {
  const addr = k32.getExportByName(api);
  Interceptor.attach(addr, {
    onEnter(args) {
      try { this.name = api === 'CreateFileW' ? args[0].readUtf16String() : args[0].readAnsiString(); }
      catch (e) { this.name = null; }
    },
    onLeave(retval) {
      if (this.name && this.name.toLowerCase().indexOf('pipe') >= 0) {
        pipes[retval.toString()] = this.name;
        send({ kind: 'open', name: this.name });
      }
    }
  });
}
for (const api of ['WriteFile', 'ReadFile']) {
  const addr = k32.getExportByName(api);
  Interceptor.attach(addr, {
    onEnter(args) {
      const name = pipes[args[0].toString()];
      if (!name) return;
      try {
        const len = args[2].toInt32();
        if (len <= 0 || len > 65536) return;
        const bytes = new Uint8Array(args[1].readByteArray(len));
        let hex = '', text = '';
        for (let i = 0; i < bytes.length; i++) {
          hex += bytes[i].toString(16).padStart(2, '0');
          text += (bytes[i] >= 32 && bytes[i] < 127) ? String.fromCharCode(bytes[i]) : '.';
        }
        send({ kind: api, name: name, len: len, hex: hex, text: text });
      } catch (e) {}
    }
  });
}
send({ kind: 'hooked' });
"""


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    pid = frida.spawn([EXE], stdio="inherit")
    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        kind = payload.get("kind")
        if kind in ("WriteFile", "ReadFile"):
            print(f"[{kind}] {payload['name']} len={payload['len']}")
            print(f"    hex={payload['hex'][:400]}")
            print(f"    txt={payload['text'][:200]}")
        else:
            print(f"[{kind}] {payload}")

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(f"[info] settings app pid={pid}, capturing {seconds:.0f}s")
    time.sleep(seconds)
    try:
        script.unload()
    except Exception:
        pass
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
