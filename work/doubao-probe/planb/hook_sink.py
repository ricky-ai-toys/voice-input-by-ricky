"""Plan B: what exactly does the engine post to the registered notify sink?

The engine answers our `PeekVoiceCommit` polls with nothing, but it does post window messages
to the sink hwnd while dictating. This tool hooks `PostMessageW`/`SendMessageW` *inside the
engine* so the payload (which lives in the engine's address space) can be dumped, and prints
the message id, wparam/lparam plus any string or struct the pointers lead to.

    python hook_sink.py scratch [--hold 8]
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
const user32 = Process.getModuleByName('user32.dll');
function modOf(a) {
  const m = Process.findModuleByAddress(a);
  return m ? (m.name + '+0x' + a.sub(m.base).toString(16)) : a.toString();
}
function tryString(p) {
  if (p.isNull()) return '';
  try {
    const s = p.readUtf16String(160);
    if (s && s.length > 1) return s;
  } catch (e) {}
  try {
    const s = p.readUtf8String(160);
    if (s && s.length > 1) return s;
  } catch (e) {}
  return '';
}
const postThread = user32.getExportByName('PostThreadMessageW');
if (postThread) {
  Interceptor.attach(postThread, {
    onEnter(args) {
      send({ kind: 'msg', api: 'PostThreadMessageW', hwnd: 'tid=' + args[0].toString(),
             msg: args[1].toInt32() & 0xffff, wparam: args[2].toString(),
             lparam: args[3].toString(), from: 'PostThreadMessage' });
    }
  });
}
for (const api of ['PostMessageW', 'SendMessageW']) {
  let addr = null;
  try { addr = user32.getExportByName(api); } catch (e) { continue; }
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      const msg = args[1].toInt32() & 0xffff;
      // only custom / app-range messages and the two ids we saw earlier
      if (msg < 0x0300 && msg !== 0x000f) return;
      const info = { kind: 'msg', api: api, hwnd: args[0].toString(), msg: msg,
                     wparam: args[2].toString(), lparam: args[3].toString() };
      info.lparam_str = tryString(args[3]);
      info.wparam_str = tryString(args[2]);
      // if the payload is a struct, show the first few fields as pointers/ints
      try {
        const p = args[3];
        if (!p.isNull()) {
          const words = [];
          for (let i = 0; i < 4; i++) words.push(p.add(i * 8).readPointer().toString());
          info.lparam_words = words;
        }
      } catch (e) {}
      try {
        const bt = Thread.backtrace(this.context, Backtracer.ACCURATE).map(modOf).slice(0, 4);
        info.from = bt.join(' <- ');
      } catch (e) {}
      send(info);
    }
  });
}
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 8.0
    exe = os.path.join(runtime, "ImeService.exe")
    device = frida.get_local_device()
    log = open(os.path.join(runtime, "hook_sink_server.log"), "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session_fr = frida.attach(pid)
    script = session_fr.create_script(JS)
    hits: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "msg":
            hits.append(payload)
            print(f"   [{payload['api']}] msg=0x{payload['msg']:04X} hwnd={payload['hwnd']} "
                  f"wp={payload['wparam']} lp={payload['lparam']}")
            if payload.get("lparam_str"):
                print(f"        lparam str = {payload['lparam_str'][:120]!r}")
            if payload.get("wparam_str"):
                print(f"        wparam str = {payload['wparam_str'][:120]!r}")
            if payload.get("from"):
                print(f"        from {payload['from']}")
            if payload.get("lparam_words"):
                print(f"        lparam words = {payload['lparam_words']}")
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
    time.sleep(hold)
    for attempt in range(10):
        user32.keybd_event(0x41, 0, 0, None)
        time.sleep(0.05)
        user32.keybd_event(0x41, 0, 2, None)
        time.sleep(0.3)
        log.flush()
        tail = open(os.path.join(runtime, "hook_sink_server.log"), "rb").read()[-20000:]
        if b"PRESS_STOP" in tail:
            print(f"[key] stop accepted after {attempt + 1} attempt(s)")
            break
    user32.keybd_event(0xA5, 0, 2, None)
    print("[key] Right Alt up")
    time.sleep(8.0)
    pipe.close()
    print(f"[done] {len(hits)} messages captured")
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
