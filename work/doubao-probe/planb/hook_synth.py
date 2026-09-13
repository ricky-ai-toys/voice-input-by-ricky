"""Plan B: catch the engine synthesizing its own key events.

On release the voice hook "eats" the real key-up and injects synthetic release events
(`SynthesizeMetaRelease ... injected`) to suppress the Alt menu. Because our copy also removes
the injected-key filter, those synthetic events come back into the hook and cancel the session
instead of stopping it. This tool hooks `keybd_event`/`SendInput` *inside the engine* and prints
exactly what it injects (vk, scan, flags, dwExtraInfo), which is what we need to discriminate
"our" injected keys from "the engine's".

    python hook_synth.py scratch [--hold 6]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import frida

from pipe_client import Pipe, pb_str
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_key
from tsf_activate import activate_doubao_for_process

JS = r"""
const user32 = Process.getModuleByName('user32.dll');
function modOf(a) {
  const m = Process.findModuleByAddress(a);
  return m ? (m.name + '+0x' + a.sub(m.base).toString(16)) : a.toString();
}
const kbe = user32.getExportByName('keybd_event');
Interceptor.attach(kbe, {
  onEnter(args) {
    send({ kind: 'keybd_event', vk: args[0].toInt32(), scan: args[1].toInt32(),
           flags: args[2].toInt32(), extra: args[3].toString(),
           from: Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOf).slice(0, 3).join(' <- ') });
  }
});
const si = user32.getExportByName('SendInput');
Interceptor.attach(si, {
  onEnter(args) {
    const n = args[0].toInt32();
    const arr = args[1];
    const info = { kind: 'SendInput', n: n, items: [],
                   from: Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOf).slice(0, 3).join(' <- ') };
    try {
      for (let i = 0; i < Math.min(n, 4); i++) {
        const p = arr.add(i * 40);           // sizeof(INPUT) == 40 on x64
        const type = p.readU32();
        // the union starts at offset 8 (alignas 8 on x64), so KEYBDINPUT is at p+8
        const vk = p.add(8).readU16();
        const scan = p.add(10).readU16();
        const flags = p.add(12).readU32();
        const extra = p.add(24).readPointer().toString();
        info.items.push({ type: type, vk: vk, scan: scan, flags: flags, extra: extra });
      }
    } catch (e) { info.error = String(e); }
    send(info);
  }
});
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 6.0
    exe = os.path.join(runtime, "ImeService.exe")
    device = frida.get_local_device()
    log = open(os.path.join(runtime, "hook_synth_server.log"), "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session_fr = frida.attach(pid)
    script = session_fr.create_script(JS)
    hits: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "keybd_event":
            hits.append(payload)
            print(f"   [keybd_event] vk=0x{payload['vk']:02X} scan=0x{payload['scan']:02X} "
                  f"flags=0x{payload['flags']:X} extra={payload['extra']}")
            print(f"        from {payload.get('from')}")
        elif payload.get("kind") == "SendInput":
            hits.append(payload)
            print(f"   [SendInput] n={payload['n']} items={payload['items']}")
            print(f"        from {payload.get('from')}")
        else:
            print(f"   [hook] {payload}")

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(f"[info] engine pid={pid}")
    time.sleep(2.0)

    sp = SettingsPipe()
    print(f"[arm] {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()
    hwnd = make_foreground_window()
    print(f"[host] hwnd=0x{hwnd:X} pid={os.getpid()}")
    print(f"[tsf] activate -> 0x{activate_doubao_for_process(verbose=False):08X}")

    pipe = Pipe(timeout=35.0)
    my_pid = os.getpid()
    for op, body in ((0x02, b""),
                     (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb")),
                     (0x1B, bytes.fromhex("0801")),
                     (0x0C, bytes.fromhex("08f8071801")),
                     (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe")),
                     (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little")),
                     (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
                      + lp("planb") + lp("python.exe"))):
        pipe.call(op, body)
    print("[ctx] host context registered")

    send_key(0xA5)
    print("[key] Right Alt down")
    time.sleep(hold)
    send_key(0xA5, up=True)
    print("[key] Right Alt up")
    time.sleep(3.0)
    pipe.close()
    print(f"[done] {len(hits)} injection(s) captured")
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
