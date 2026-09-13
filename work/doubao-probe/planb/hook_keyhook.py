"""Plan B: watch the engine's own low-level keyboard hook decide about our key press.

The server logs `voice key hook installed (ref=1) on dedicated thread`, i.e. the voice
hotkey is detected inside ImeService.exe and never travels over the pipe. To learn what that
hook accepts, hook SetWindowsHookExW in the engine, then hook the hook procedure itself and
log every key event it sees (vkCode, flags, injected bit).

    python hook_keyhook.py scratch [--key 165] [--hold 2.0]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import frida

from pipe_client import Pipe, log_grep, start_server

JS = r"""
const user32 = Process.getModuleByName('user32.dll');
const exeMod = Process.getModuleByName('ImeService.exe');
const hooks = {};
const setHook = user32.getExportByName('SetWindowsHookExW');
Interceptor.attach(setHook, {
  onEnter(args) {
    this.id = args[0].toInt32();
    this.proc = args[1];
    this.mod = args[2];
  },
  onLeave(retval) {
    send({ kind: 'sethook', idHook: this.id, proc: this.proc.toString(),
           hhook: retval.toString(), mod: this.mod.toString() });
    if (this.id === 13) {          // WH_KEYBOARD_LL
      hooks[retval.toString()] = this.proc;
      try {
        Interceptor.attach(this.proc, {
          onEnter(args) {
            const info = args[2];              // LPARAM -> KBDLLHOOKSTRUCT*
            const data = { kind: 'key', nCode: args[0].toInt32(), wParam: args[1].toInt32() };
            try {
              const k = info;
              data.vk = k.readU32();
              data.scan = k.add(4).readU32();
              data.flags = k.add(8).readU32();
              data.injected = (data.flags & 0x10) !== 0;
              data.extra = k.add(16).readPointer().toString();
              // VoiceKeyHookProc starts by checking a global state object: NULL or
              // [+0xe08]==0 or [+0xe09]!=0 makes it bail out before any voice logic.
              const g = exeMod.base.add(0x162a7b8).readPointer();
              data.state_ptr = g.toString();
              if (!g.isNull()) {
                data.f_e08 = g.add(0xe08).readU8();
                data.f_e09 = g.add(0xe09).readU8();
              }
            } catch (e) { data.err = String(e); }
            send(data);
          },
          onLeave(retval) { send({ kind: 'keyret', retval: retval.toString() }); },
        });
        send({ kind: 'info', message: 'keyboard hook procedure hooked at ' + this.proc });
      } catch (e) {
        send({ kind: 'warn', message: 'hook proc attach failed: ' + e });
      }
    }
  },
});
send({ kind: 'info', message: 'SetWindowsHookExW hooked' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    key = 165
    hold = 2.0
    if "--key" in sys.argv:
        key = int(sys.argv[sys.argv.index("--key") + 1], 0)
    if "--hold" in sys.argv:
        hold = float(sys.argv[sys.argv.index("--hold") + 1])

    # spawn through frida so the hook script is live *before* the engine installs its own
    # keyboard hook during startup
    exe = os.path.join(runtime, "ImeService.exe")
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    print(f"[info] spawned engine pid={pid}")
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print(f"   [hook] {m.get('payload')}", flush=True))
    script.load()
    frida.resume(pid)

    pipe = Pipe(timeout=35.0)
    print("[ok] pipe connected")
    time.sleep(1.0)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
    print(f"[key] pressing 0x{key:02X} for {hold}s (injected)")
    user32.keybd_event(key, 0, 0, None)
    time.sleep(hold)
    user32.keybd_event(key, 0, 2, None)
    time.sleep(0.5)

    res = pipe.call(0x17, b"")
    print(f"[peek] {res}")
    pipe.close()
    log_grep(runtime, 15)
    frida.kill(pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
