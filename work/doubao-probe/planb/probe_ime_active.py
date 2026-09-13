"""Plan B: what makes the engine consider a pid "IME active"?

`VoiceKeyHookProc` refuses the matched hotkey with

    [VHK][Trig] ACTIVATION but not allowed (ime not foreground-active), pass through

so the last gate is that check. `rpc.dll` exports `IsPidImeActive(pid)` for exactly that
question; this tool asks the *engine's own* copy of that function (through frida) before and
after each RPC we send, so we can see which call flips it.

    python probe_ime_active.py scratch
"""
from __future__ import annotations

import os
import sys
import time

import frida

from pipe_client import Pipe, pb_int, pb_str
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window
from tsf_activate import activate_doubao_for_process

JS = r"""
const rpcMod = Process.getModuleByName('rpc.dll');
const sym = rpcMod.enumerateExports().find(e => e.name.indexOf('IsPidImeActive') >= 0);
const getFocus = rpcMod.enumerateExports().find(e => e.name.indexOf('GetFocusOwnerPid') >= 0);
let isActive = null, focusPid = null;
if (sym) isActive = new NativeFunction(sym.address, 'bool', ['uint32']);
if (getFocus) focusPid = new NativeFunction(getFocus.address, 'uint32', []);
rpc.exports = {
  query(pid) {
    return {
      exported: !!sym,
      active: isActive ? isActive(pid) : null,
      focusPid: focusPid ? focusPid() : null,
    };
  }
};
send({ kind: 'ready', exported: !!sym, focusExported: !!getFocus });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    exe = os.path.join(runtime, "ImeService.exe")
    device = frida.get_local_device()
    log = open(os.path.join(runtime, "probe_ime_active_server.log"), "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print(f"[hook] {m.get('payload')}", flush=True))
    script.load()
    frida.resume(pid)
    time.sleep(2.0)

    me = os.getpid()
    hwnd = make_foreground_window()
    print(f"[info] engine pid={pid}, our pid={me}, hwnd=0x{hwnd:X}")

    def query(label: str) -> None:
        try:
            result = script.exports_sync.query(me)
            print(f"[query] {label:28s} {result}")
        except Exception as exc:  # noqa: BLE001
            print(f"[query] {label:28s} failed: {exc}")

    query("before anything")
    sp = SettingsPipe()
    sp.call(request("settings.setVoiceTryoutActive", {"active": True, "cookie": int(time.time())}))
    sp.close()
    query("after voiceTryout(true)")

    pipe = Pipe(timeout=35.0)
    steps = [
        (0x02, b"", "Activate"),
        (0x06, (0x48F8).to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
         "FocusIn"),
        (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
         "RegisterTsfNotifySink"),
        (0x14, lp("") + lp("") + lp("") + int(hwnd).to_bytes(8, "little") + lp("planb") + lp("python.exe"),
         "UpdateHostContext"),
        (0x1B, bytes.fromhex("0801"), "SetUIElementShowState"),
    ]
    for op, body, name in steps:
        res = pipe.call(op, body)
        print(f"[send] 0x{op:02X} {name} -> {res if res is None else res[2]}")
        query(f"after {name}")

    activate_doubao_for_process(verbose=False)
    time.sleep(0.5)
    query("after TSF ActivateProfile")

    pipe.close()
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
