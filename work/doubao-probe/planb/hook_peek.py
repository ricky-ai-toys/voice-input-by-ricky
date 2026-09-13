"""Plan B: does the engine ever actually hand out transcript text?

Hooks the engine-side implementation of `PeekVoiceCommit` (`ImeService.exe+0x80F530`) while a
real session runs, and prints the session id and text it produces. If the hook stays empty the
text takes the notify-sink path instead (posted window messages), which is what the earlier
message observation suggested.

    python hook_peek.py scratch [--hold 8]
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
from try_voice import lp, make_foreground_window
from tsf_activate import activate_doubao_for_process

JS = r"""
const exe = Process.getModuleByName('ImeService.exe');
function readStdString(ptr) {
  try {
    const size = ptr.add(0x10).readU64().toNumber();
    const cap = ptr.add(0x18).readU64().toNumber();
    if (size <= 0 || size > 4096) return '';
    const data = cap >= 0x10 ? ptr.readPointer() : ptr;
    return data.readUtf8String(size);
  } catch (e) { return '<err ' + e + '>'; }
}
// The service implementation vtable lives at ImeService.exe+0x1009E78; entry[n] handles op n.
// Every key the engine's own voice hook sees (does a synthetic key-up reach it?).
Interceptor.attach(exe.base.add(0x7426c0), {
  onEnter(args) {
    try {
      const k = args[2];
      send({ kind: 'key', nCode: args[0].toInt32(), wParam: args[1].toInt32(),
             vk: k.readU32(), scan: k.add(4).readU32(), flags: k.add(8).readU32(),
             extra: k.add(16).readPointer().toString() });
    } catch (e) { send({ kind: 'warn', message: 'key hook: ' + e }); }
  }
});
const vt = exe.base.add(0x1009e78);
const entries = [];
for (let op = 0; op <= 0x1c; op++) {
  const fn = vt.add(op * 8).readPointer();
  const mod = Process.findModuleByAddress(fn);
  const label = mod ? (mod.name + '+0x' + fn.sub(mod.base).toString(16)) : fn.toString();
  entries.push('0x' + op.toString(16) + '->' + label);
  try {
    Interceptor.attach(fn, {
      onEnter(args) {
        this.op = op;
        this.session = args[1];
        this.text = args[2];
        if (op === 0x17 || op === 0x18) {
          send({ kind: 'enter', op: op, arg1: args[1].toString(), arg2: args[2].toString() });
        }
      },
      onLeave(retval) {
        if (op === 0x17 || op === 0x18) {
          let session = -1, text = '';
          try { session = this.session.readU64().toNumber(); } catch (e) {}
          try { text = readStdString(this.text); } catch (e) {}
          send({ kind: 'peek', op: this.op, ret: retval.toInt32(), session: session, text: text });
        } else {
          send({ kind: 'hit', op: this.op });
        }
      }
    });
  } catch (e) { send({ kind: 'warn', message: 'hook ' + label + ' failed: ' + e }); }
}
send({ kind: 'vtable', entries: entries });
if (false) {
  // placeholder so the old ack hook comment stays meaningful
}
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 8.0
    exe = os.path.join(runtime, "ImeService.exe")
    device = frida.get_local_device()
    log = open(os.path.join(runtime, "hook_peek_server.log"), "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session_fr = frida.attach(pid)
    script = session_fr.create_script(JS)

    events: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "ready":
            print(f"   [hook] {payload}")
        elif payload.get("kind") == "vtable":
            print("   [vtable] " + " ".join(payload["entries"]))
        elif payload.get("kind") == "warn":
            print(f"   [warn] {payload['message']}")
        elif payload.get("kind") == "peek":
            events.append(payload)
            if payload.get("session") or payload.get("text"):
                print(f"   [peek impl] session={payload['session']} ret={payload['ret']} "
                      f"text={payload['text']!r}")
        elif payload.get("kind") == "enter":
            print(f"   [enter] op=0x{payload['op']:02X} arg1={payload['arg1']} "
                  f"arg2={payload['arg2']}")
        elif payload.get("kind") == "hit":
            print(f"   [hit] op=0x{payload['op']:02X}")
        elif payload.get("kind") == "key":
            print(f"   [key] vk=0x{payload['vk']:02X} wParam=0x{payload['wParam']:04X} "
                  f"flags=0x{payload['flags']:X} scan=0x{payload.get('scan', 0):X} "
                  f"extra={payload.get('extra')}")
        elif payload.get("kind") == "ack":
            print(f"   [ack impl] session={payload['arg']}")

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
    for op, body, name in (
            (0x02, b"", "Activate"),
            (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb"), "ImeChanged"),
            (0x1B, bytes.fromhex("0801"), "SetUIElementShowState"),
            (0x0C, bytes.fromhex("08f8071801"), "SimpleMessage(1016,1)"),
            (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
             "FocusIn(pid)"),
            (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
             "RegisterTsfNotifySink"),
            (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
             + lp("planb") + lp("python.exe"), "UpdateHostContext")):
        res = pipe.call(op, body)
        print(f"[ctx] {name:22s} -> {res if res is None else res[2]}")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
    user32.keybd_event(0xA5, 0, 0, None)
    print("[key] Right Alt down")
    time.sleep(2.0)
    # is the engine's hook still receiving keys while it records?
    user32.keybd_event(0x41, 0, 0, None)
    time.sleep(0.1)
    user32.keybd_event(0x41, 0, 2, None)
    print("[key] probe key 'A' during the session")
    deadline = time.time() + hold
    while time.time() < deadline:
        time.sleep(0.5)
        pipe.call(0x17, b"\x00\x00\x00\x00")
    user32.keybd_event(0xA5, 0, 2, None)
    print("[key] Right Alt up")
    for i in range(20):
        time.sleep(0.5)
        pipe.call(0x17, b"\x00\x00\x00\x00")

    pipe.close()
    print(f"[done] {len(events)} peek-impl calls, "
          f"{sum(1 for e in events if e.get('session'))} with a session")
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
