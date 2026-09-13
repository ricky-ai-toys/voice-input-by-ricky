"""What does the engine copy actually depend on outside its own folder?

Hooks the registry and file APIs inside our private engine while it runs a real recognition
session, then prints every key it opened/created and every file it touched that lives outside
the runtime directory. This is the evidence for "does it run where Doubao was never installed".

    python audit_deps.py scratch
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import frida

import pipe_client
from planb_voice_input import FFPLAY, SELFTEST_PATCHES, PlanBVoice
from try_voice import send_key

WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audio", "three_zh_16k.wav")
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence",
                      "dependency_audit.txt")

JS = r"""
rpc.exports = {
  read(off, len) {
    const exe = Process.getModuleByName('ImeService.exe');
    return Array.from(new Uint8Array(exe.base.add(off).readByteArray(len)));
  },
  write(off, bytes) {
    const exe = Process.getModuleByName('ImeService.exe');
    const addr = exe.base.add(off);
    Memory.protect(addr, bytes.length, 'rwx');
    addr.writeByteArray(bytes);
    Memory.protect(addr, bytes.length, 'r-x');
    return true;
  }
};
const reg = {};
const ROOTS = {
  '80000000': 'HKCR', '80000001': 'HKCU', '80000002': 'HKLM',
  '80000003': 'HKU', '80000005': 'HKCC', '80000006': 'HKPD', '80000007': 'HKDD'
};
function rootName(h) {
  var s = h.toString().replace(/^0x/, '').toLowerCase();
  if (s.length > 8) s = s.slice(-8);
  return ROOTS[s] || null;
}
function keyOf(h) {
  return rootName(h) || reg[h.toString()] || '<handle:' + h.toString() + '>';
}
const CLEAN = __SIMULATE_CLEAN__;      // pretend the vendor product was never installed
function exp(mod, name) {          // frida 17 dropped the static Module.getExportByName
  if (typeof Module.getExportByName === 'function') return Module.getExportByName(mod, name);
  if (typeof Module.findExportByName === 'function') return Module.findExportByName(mod, name);
  return Process.getModuleByName(mod).getExportByName(name);
}

['RegOpenKeyExW', 'RegCreateKeyExW'].forEach(function (api) {
  const p = exp('advapi32.dll', api);
  Interceptor.attach(p, {
    onEnter(args) {
      this.parent = keyOf(args[0]);
      this.sub = args[1].isNull() ? '' : args[1].readUtf16String();
      this.block = CLEAN && this.parent === 'HKLM' && this.sub.indexOf('SOFTWARE\\DoubaoIme') === 0;
    },
    onLeave(ret) {
      if (this.block) ret.replace(2);          // ERROR_FILE_NOT_FOUND, as on a clean machine
      const ok = ret.toInt32() === 0;
      const full = this.parent + '\\' + this.sub;
      if (ok) reg[ret.toString()] = full;
      send({ t: 'reg', api: api, key: full, ok: ok, blocked: !!this.block });
    }
  });
});

['RegQueryValueExW', 'RegSetValueExW', 'RegDeleteValueW'].forEach(function (api) {
  const p = exp('advapi32.dll', api);
  Interceptor.attach(p, {
    onEnter(args) {
      this.key = keyOf(args[0]);
      this.name = args[1].isNull() ? '' : args[1].readUtf16String();
    },
    onLeave(ret) {
      if (ret.toInt32() === 0) send({ t: 'regvalue', api: api, key: this.key, name: this.name });
    }
  });
});

const cfw = exp('kernel32.dll', 'CreateFileW');
Interceptor.attach(cfw, {
  onEnter(args) { this.path = args[0].isNull() ? '' : args[0].readUtf16String(); },
  onLeave(ret) {
    if (ret.toInt32() !== -1) send({ t: 'file', path: this.path });
  }
});
send({ t: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "scratch")
    exe = os.path.join(runtime, "ImeService.exe")

    # spawn suspended so the hooks are in place *before* the engine reads anything
    pipe_client._snapshot_user_config()
    device = frida.get_local_device()
    pid = device.spawn([exe], cwd=runtime)
    print(f"[audit] engine pid={pid} (suspended)")

    seen_reg, seen_val, seen_file = set(), set(), set()
    session = device.attach(pid)
    clean = "--simulate-clean" in sys.argv
    script = session.create_script(JS.replace("__SIMULATE_CLEAN__", "true" if clean else "false"))
    print(f"[audit] simulate-clean = {clean}")

    def on_message(message, _data):
        if message.get("type") != "send":
            print(f"[audit] frida {message.get('type')}: {message.get('description') or message}")
            return
        payload = message["payload"]
        if payload["t"] == "ready":
            print("[audit] hooks installed")
        elif payload["t"] == "reg":
            seen_reg.add(f'{payload["api"]}: {payload["key"]}  [{"ok" if payload["ok"] else "failed"}]')
        elif payload["t"] == "regvalue":
            seen_val.add(f'{payload["key"]} :: {payload["name"]} ({payload["api"]})')
        else:
            path = payload["path"]
            if not path.lower().startswith(runtime.lower()):
                seen_file.add(path)

    script.on("message", on_message)
    script.load()
    device.resume(pid)

    class SpawnedProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def terminate(self) -> None:
            try:
                device.kill(pid)
            except Exception:
                pass

    voice = PlanBVoice(runtime, paste=False, use_tsf=False, proc=SpawnedProc(pid))

    # the injected hotkey needs the vendor's filters relaxed - a second frida session on a
    # frida-spawned process is refused, so patch through the session we already own
    originals = []
    for off, expected, patch in SELFTEST_PATCHES:
        if bytes(script.exports_sync.read(off, len(expected))) != expected:
            print(f"[audit] 0x{off:X} unexpected, skipped")
            continue
        script.exports_sync.write(off, list(patch))
        originals.append((off, expected))
    print(f"[audit] relaxed {len(originals)} engine filter(s)")

    voice.arm()
    player = None
    if os.path.exists(FFPLAY) and os.path.exists(WAV):
        player = subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", WAV],
                                  creationflags=0x00000008)
    time.sleep(0.3)
    voice.on_press()
    send_key(0xA5)
    time.sleep(7.0)
    send_key(0xA5, up=True)
    voice.on_release()
    if player:
        player.kill()
    for off, original in originals:
        script.exports_sync.write(off, list(original))
    print(f"[audit] restored {len(originals)} engine filter(s)")
    script.unload()
    voice.pipe.close()
    voice.proc.terminate()

    print(f"\n[audit] registry keys opened/created ({len(seen_reg)}):")
    lines = [f"registry keys opened/created ({len(seen_reg)}):"]
    lines += ["   " + entry for entry in sorted(seen_reg)]
    print(f"\n[audit] values read/written ({len(seen_val)}):")
    lines.append("")
    lines.append(f"values read/written ({len(seen_val)}):")
    for entry in sorted(seen_val):
        print("   " + entry)
        lines.append("   " + entry)
    print(f"\n[audit] files outside the runtime dir ({len(seen_file)}):")
    lines.append("")
    lines.append(f"files outside the runtime dir ({len(seen_file)}):")
    for entry in sorted(seen_file):
        print("   " + entry)
        lines.append("   " + entry)
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\n[audit] report -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
