"""Plan B: locate the code that refuses the voice hotkey.

The hook logs

    [VHK][Trig] ACTIVATION but not allowed (ime not foreground-active), pass through

Static xrefs to that format string do not exist (the logger takes a descriptor built at load
time), so this tool hooks the engine's log write and takes a backtrace when that exact line is
emitted while we inject Right Alt. The resulting ImeService.exe+offset is the branch to study
(and, optionally, to patch).

    python find_voicegate.py scratch
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import frida

from pipe_client import Pipe, pb_int, pb_str
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window
from tsf_activate import activate_doubao_for_process

MARKER = b"not allowed"

JS = r"""
function modOf(addr) {
  const m = Process.findModuleByAddress(addr);
  return m ? (m.name + '+0x' + addr.sub(m.base).toString(16)) : addr.toString();
}
const k32 = Process.getModuleByName('kernel32.dll');
// does the voice gate consult rpc::IsPidImeActive? and for which pid?
try {
  const rpcMod = Process.getModuleByName('rpc.dll');
  const sym = rpcMod.enumerateExports().find(e => e.name.indexOf('IsPidImeActive') >= 0);
  if (sym) {
    Interceptor.attach(sym.address, {
      onEnter(args) { this.pid = args[0].toInt32(); },
      onLeave(retval) { send({ kind: 'isactive', pid: this.pid, result: retval.toInt32() }); }
    });
    send({ kind: 'info', message: 'IsPidImeActive hooked' });
  }
} catch (e) { send({ kind: 'warn', message: 'isactive hook failed: ' + e }); }
// the trigger set is walked at ImeService+0x92C0C6 (`call qword ptr [rax+8]`); logging the
// target tells us which lambda produced the "not allowed" line
try {
  const exe = Process.getModuleByName('ImeService.exe');
  Interceptor.attach(exe.base.add(0x931e30), {   // the "[VHK] ..." log helper
    onEnter(args) {
      const info = { kind: 'log', caller: modOf(this.returnAddress),
                     rcx: this.context.rcx.toString(), rdx: this.context.rdx.toString(),
                     r8: this.context.r8.toString(), r9: this.context.r9.toString() };
      for (const reg of ['rcx', 'rdx', 'r8', 'r9']) {
        try {
          const s = this.context[reg].readUtf8String(120);
          if (s && /[ -~]{6,}/.test(s)) info[reg + '_str'] = s;
        } catch (e) {}
      }
      send(info);
    }
  });
} catch (e) { send({ kind: 'warn', message: 'trigger hook failed: ' + e }); }
for (const api of ['WriteFile', 'WriteConsoleA', 'WriteConsoleW']) {
  let addr = null;
  try { addr = k32.getExportByName(api); } catch (e) { continue; }
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      try {
        const len = args[2].toInt32();
        if (len <= 0 || len > 8192) return;
        const bytes = new Uint8Array(args[1].readByteArray(len));
        let text = '';
        for (let i = 0; i < bytes.length; i++) {
          text += (bytes[i] >= 32 && bytes[i] < 127) ? String.fromCharCode(bytes[i]) : '.';
        }
        if (text.indexOf('not allowed') >= 0) {
          const bt = Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOf);
          send({ kind: 'gate', api: api, text: text.slice(0, 200), backtrace: bt.slice(0, 12) });
        }
      } catch (e) {}
    }
  });
}
send({ kind: 'hooked' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    exe = os.path.join(runtime, "ImeService.exe")
    log = open(os.path.join(runtime, "voicegate_server.log"), "wb")
    device = frida.get_local_device()
    device.on("output", lambda pid_, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session = frida.attach(pid)
    script = session.create_script(JS)
    hits: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "gate":
            hits.append(payload)
            print(f"[gate] {payload['text'][:120]}")
            for frame in payload["backtrace"]:
                print(f"        {frame}")
        elif payload.get("kind") == "log":
            text = payload.get("rcx_str") or payload.get("rdx_str") or payload.get("r8_str") or payload.get("r9_str")
            if text and "VHK" in text:
                print(f"[log] caller={payload['caller']} text={text[:110]}")
        elif payload.get("kind") == "isactive":
            print(f"[isactive] pid={payload['pid']} -> {payload['result']}")
        else:
            print(f"[{payload.get('kind')}] {payload}")

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(f"[info] engine pid={pid}")

    pipe = Pipe(timeout=35.0)
    sp = SettingsPipe()
    print(f"[arm] {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()
    hwnd = make_foreground_window()
    print(f"[tsf] activate -> 0x{activate_doubao_for_process(verbose=False):08X}")
    for op, body in ((0x02, b""), (0x1B, bytes.fromhex("0801")),
                     (0x06, (0x48F8).to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe")),
                     (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little")),
                     (0x14, lp("") + lp("") + lp("") + int(hwnd).to_bytes(8, "little") + lp("planb") + lp("python.exe"))):
        pipe.call(op, body)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
    user32.keybd_event(0xA5, 0, 0, None)
    time.sleep(1.5)
    user32.keybd_event(0xA5, 0, 2, None)
    time.sleep(1.0)

    pipe.close()
    try:
        script.unload()
    except Exception:
        pass
    try:
        frida.kill(pid)
    except Exception:
        pass
    print(f"[info] captured {len(hits)} gate hit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
