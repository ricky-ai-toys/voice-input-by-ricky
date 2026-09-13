"""Plan B: does the engine ever run `Controller::CheckCommit` in our sessions?

`slot_PeekVoiceCommit` answers `session=0 bytes=0`, i.e. the controller never stored a commit.
`Controller::CheckCommit` (ImeService.exe+0x77D670, logs "commit len is {}.") is the function
that would store it, so this tool hooks it and runs one full session: if it never fires, the
commit path is never reached in our setup and the reason is upstream of the client hand-off.

    python hook_commit.py scratch [--hold 3] [--click]
"""
from __future__ import annotations

import os
import re
import sys
import time

import frida

from commit_probe import release_all
from pipe_client import Pipe, pb_int, pb_str
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_click, send_key
from tsf_activate import activate_doubao_for_process

JS = r"""
const exe = Process.getModuleByName('ImeService.exe');
function hook(off, name, logArgs) {
  try {
    Interceptor.attach(exe.base.add(off), {
      onEnter(args) {
        const info = { kind: 'call', name: name };
        if (logArgs) {
          try { info.a0 = args[0].toString(); } catch (e) {}
          try { info.str = args[1].readUtf8String(64); } catch (e) {}
        }
        send(info);
      },
      onLeave(retval) { send({ kind: 'ret', name: name, ret: retval.toInt32() }); }
    });
    send({ kind: 'info', message: 'hooked ' + name });
  } catch (e) { send({ kind: 'warn', message: name + ': ' + e }); }
}
hook(0x77d670, 'Controller::CheckCommit', true);
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 3.0
    use_click = "--click" in sys.argv
    log_path = os.path.join(runtime, "pipe_client_server.log")
    exe = os.path.join(runtime, "ImeService.exe")

    device = frida.get_local_device()
    log = open(log_path, "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session_fr = frida.attach(pid)
    script = session_fr.create_script(JS)
    calls: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") in ("call", "ret"):
            calls.append(payload)
            print(f"   [{payload['kind']}] {payload.get('name')} "
                  f"{payload.get('str', '')!r} ret={payload.get('ret', '')}")
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
    activate_doubao_for_process(verbose=False)
    pipe = Pipe(timeout=35.0)
    my_pid = os.getpid()
    for op, body in ((0x02, b""),
                     (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb")),
                     (0x1B, bytes.fromhex("0801")),
                     (0x0C, bytes.fromhex("08f8071801")),
                     (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe")),
                     (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little")),
                     (0x08, pb_int(1, 100) + pb_int(2, 100) + pb_int(3, 20)),
                     (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
                      + lp("planb") + lp("python.exe"))):
        pipe.call(op, body)

    send_key(0xA5)
    print("[key] Right Alt down")
    time.sleep(hold)
    if use_click:
        send_click()
        print("[mouse] click stop")
    else:
        send_key(0xA5, up=True)
        print("[key] Right Alt up")
    time.sleep(6.0)
    try:
        script.unload()
    except Exception:
        pass
    pipe.close()

    blob = open(log_path, "rb").read().decode("utf-8", "replace")
    print(f"[result] CheckCommit calls: {len([c for c in calls if c.get('kind') == 'call'])}")
    for pat in ("voice startfrom", "had_result_text=1", "STOP_FAST", "slot_PeekVoiceCommit session=0"):
        print(f"   {pat}: {blob.count(pat)}")
    items = re.findall(r'"is_interim":(\w+),"text":"([^"]*)"', blob)
    print(f"   asr results: {len(items)} (non-empty {sum(1 for _, t in items if t)})")
    try:
        frida.kill(pid)
    except Exception:
        pass
    release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
