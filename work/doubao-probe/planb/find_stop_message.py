"""Plan B: find the engine's internal stop message so we can end a session without hooks.

The voice hook never touches the pipe - it posts `msg = 1007 (PRESS_START)` to the engine's main
thread (`[VHK][proc] PostToMainThread`). The hook-driven stop therefore is a sibling message.
Because Windows drops the low-level hooks a few seconds into a recording, posting that message
ourselves is the only way to stop a long session cleanly.

This tool (a) hooks the engine's message APIs to learn how PRESS_START is delivered and to
which thread, then (b) posts candidate ids to that thread and watches the log for the stop.

    python find_stop_message.py scratch [--hold 4]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import sys
import time

import frida

from commit_probe import release_all
from pipe_client import Pipe, pb_int, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_key
from tsf_activate import activate_doubao_for_process

JS = r"""
const user32 = Process.getModuleByName('user32.dll');
// does the engine take its own keyboard hook down once a session starts?
try {
  const unhook = user32.getExportByName('UnhookWindowsHookEx');
  Interceptor.attach(unhook, {
    onEnter(args) { send({ kind: 'unhook', hhook: args[0].toString() }); }
  });
  const sethook = user32.getExportByName('SetWindowsHookExW');
  Interceptor.attach(sethook, {
    onLeave(retval) { send({ kind: 'sethook', hhook: retval.toString() }); }
  });
} catch (e) { send({ kind: 'warn', message: 'hook apis: ' + e }); }
for (const api of ['PostThreadMessageW', 'PostMessageW', 'SendMessageW']) {
  let addr = null;
  try { addr = user32.getExportByName(api); } catch (e) { continue; }
  if (!addr) continue;
  Interceptor.attach(addr, {
    onEnter(args) {
      if (api === 'PostThreadMessageW') {
        send({ kind: 'post', api: api, target: 'tid ' + args[0].toString(),
               msg: args[1].toInt32() & 0xffff, wp: args[2].toString(), lp: args[3].toString() });
      } else {
        send({ kind: 'post', api: api, target: args[0].toString(),
               msg: args[1].toInt32() & 0xffff, wp: args[2].toString(), lp: args[3].toString() });
      }
    }
  });
}
send({ kind: 'ready' });
"""


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 4.0
    log_path = os.path.join(runtime, "pipe_client_server.log")
    exe = os.path.join(runtime, "ImeService.exe")
    device = frida.get_local_device()
    log = open(os.path.join(runtime, "find_stop_server.log"), "wb")
    device.on("output", lambda pid, fd, data: log.write(data) or log.flush())
    pid = frida.spawn([exe], cwd=runtime, stdio="pipe")
    session_fr = frida.attach(pid)
    script = session_fr.create_script(JS)
    posts: list[dict] = []

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "post":
            posts.append(payload)
        elif payload.get("kind") in ("unhook", "sethook"):
            print(f"   [hook-api] {payload}")
        else:
            print(f"   [hook] {payload}")

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(f"[info] engine pid={pid}")
    time.sleep(2.0)

    # the private copy's own config guard still applies; start_server would have spawned a
    # second engine, so arm the settings channel of *this* instance directly
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
    print("--- messages the engine posted while starting the session ---")
    for entry in posts:
        print(f"   [{entry['api']}] target={entry['target']} msg={entry['msg']} "
              f"wp={entry['wp']} lp={entry['lp']}")

    # the last message with a 0x3e8..0x400 id tells us the delivery target
    target_tid = None
    for entry in posts:
        if entry["api"] == "PostThreadMessageW" and entry["target"].startswith("tid "):
            target_tid = int(entry["target"].split()[1], 0)
    print(f"[info] candidate main thread id: {target_tid}")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
    stopped = None
    if target_tid:
        for msg in list(range(1006, 1024)):
            mark = os.path.getsize(log_path) if os.path.exists(log_path) else 0
            ok = user32.PostThreadMessageW(target_tid, msg, 0, 0)
            time.sleep(0.6)
            with open(log_path, "rb") as fh:
                fh.seek(mark)
                new = fh.read().decode("utf-8", "replace")
            hits = [l.strip() for l in new.splitlines()
                    if re.search(r"STOP|stop|finalize|commit", l) and "PeekVoiceCommit" not in l]
            print(f"   msg={msg} ok={ok} new lines={len(hits)}")
            for line in hits[:2]:
                print("        " + line[:160])
            if hits:
                stopped = msg
                break
    print(f"[result] stop message = {stopped}")
    send_key(0xA5, up=True)
    pipe.close()
    try:
        frida.kill(pid)
    except Exception:
        pass
    release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
