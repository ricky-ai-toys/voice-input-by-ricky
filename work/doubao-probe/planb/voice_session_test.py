"""Plan B: drive a full voice session through the private engine and read the text back.

Everything up to the trigger is scripted (see try_hotkeys.py). This tool adds real audio: the
test wav is played through the default output device so the engine's own microphone capture
has something to recognize, Right Alt is held while it plays, and `PeekVoiceCommit` (op 0x17)
is polled for the recognized text.

    python voice_session_test.py scratch [--wav path] [--hold 6]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import time

import frida

from pipe_client import Pipe, pb_str, start_server
from pipe_client import pb_int
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_click, send_key
from tsf_activate import activate_doubao_for_process

FFPLAY = r"E:\ffmpeg\bin\ffplay.exe"
DEFAULT_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "audio", "three_zh_16k.wav")

# The engine hands the transcript to the *TSF core* it hosts in our process, which polls and
# acknowledges it. Watching that call is the quickest way to see the text from the outside.
WATCH_JS = r"""
const mod = Process.findModuleByName('rpc.dll');
if (mod) {
  const sym = mod.enumerateExports().find(e => e.name.indexOf('PeekVoiceCommitUtf8') >= 0);
  if (sym) {
    Interceptor.attach(sym.address, {
      onEnter(args) { this.buf = args[2]; this.session = args[1]; },
      onLeave(retval) {
        let text = '';
        try { text = this.buf.readUtf8String(256); } catch (e) {}
        let session = 0;
        try { session = this.session.readU64().toNumber(); } catch (e) {}
        if (retval.toInt32() > 0 || text) {
          send({ kind: 'peek', ret: retval.toInt32(), session: session, text: text });
        }
      }
    });
    send({ kind: 'info', message: 'PeekVoiceCommitUtf8 hooked in this process' });
  } else {
    send({ kind: 'info', message: 'PeekVoiceCommitUtf8 not loaded yet' });
  }
} else {
  send({ kind: 'info', message: 'rpc.dll not loaded in this process' });
}
"""


def start_watch() -> frida.core.Script:
    session = frida.attach(os.getpid())
    script = session.create_script(WATCH_JS)

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message["payload"]
        if payload.get("kind") == "peek":
            print(f"   [core peek] ret={payload['ret']} session={payload['session']} "
                  f"text={payload['text']!r}")
        else:
            print(f"   [watch] {payload}")

    script.on("message", on_message)
    script.load()
    return script


def play_async(path: str) -> subprocess.Popen | None:
    if os.path.exists(FFPLAY):
        return subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                                creationflags=0x00000008)
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        return None
    return None


def pump_messages(hwnd: int, seconds: float, label: str) -> None:
    """The engine reports recognized text to the registered TSF notify sink by posting
    messages to our hwnd, so the host has to pump - and to print - them."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                    ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]

    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
    user32.PeekMessageW.restype = wt.BOOL
    msg = MSG()
    deadline = time.time() + seconds
    while time.time() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):   # PM_REMOVE
            if msg.message == 0x0002:                                   # WM_DESTROY
                continue
            if msg.message == 0x000F:                                   # WM_PAINT, ignore
                continue
            extra = ""
            if msg.lParam:
                try:
                    text = ctypes.c_wchar_p(msg.lParam).value
                    if text and any(" " < c < "\uffff" for c in text):
                        extra = f" text={text[:120]!r}"
                except Exception:
                    pass
                if not extra:
                    try:
                        blk = ctypes.string_at(msg.lParam, 64)
                        extra = f" bytes={blk[:24].hex()}"
                    except Exception:
                        pass
            print(f"   [msg {label}] hwnd=0x{msg.hwnd:X} id=0x{msg.message:04X} "
                  f"wparam={msg.wParam} lparam={msg.lParam}{extra}")
        time.sleep(0.05)


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    wav = os.path.abspath(sys.argv[sys.argv.index("--wav") + 1]) if "--wav" in sys.argv else \
        os.path.abspath(DEFAULT_WAV)
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 6.0
    log_path = os.path.join(runtime, "pipe_client_server.log")

    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    sp = SettingsPipe()
    print(f"[arm] {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()

    foreign = "--foreign-focus" in sys.argv
    if foreign and "--own-sink" in sys.argv:
        # our own window receives the engine's notifications, but the *foreground* app stays
        # the focus owner - that is what the hook's "allowed" check compares
        hwnd = make_foreground_window(activate=False)
        u = ctypes.WinDLL("user32")
        u.GetForegroundWindow.restype = wt.HWND
        pid_of_fg = wt.DWORD(0)
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId(u.GetForegroundWindow(), ctypes.byref(pid_of_fg))
        focus_pid = pid_of_fg.value
        print(f"[host] own sink hwnd=0x{hwnd:X}, focus owner pid={focus_pid} (foreground app)")
    elif foreign:
        u = ctypes.WinDLL("user32")
        u.GetForegroundWindow.restype = wt.HWND
        hwnd = u.GetForegroundWindow()
        pid_of_fg = wt.DWORD(0)
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_of_fg))
        focus_pid = pid_of_fg.value
        print(f"[host] reusing foreground window 0x{hwnd:X} pid={focus_pid}")
    else:
        hwnd = make_foreground_window()
        focus_pid = os.getpid()
        print(f"[host] hwnd=0x{hwnd:X} pid={focus_pid}")
    if "--no-tsf" not in sys.argv:
        print(f"[tsf] activate -> 0x{activate_doubao_for_process(verbose=False):08X}")
    watch = start_watch() if "--watch" in sys.argv else None
    pump = pump_messages if "--no-pump" not in sys.argv else (lambda *a, **k: None)

    pipe = Pipe(timeout=35.0)
    for op, body, name in (
            (0x02, b"", "Activate"),
            (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb"), "ImeChanged"),
            (0x1B, bytes.fromhex("0801"), "SetUIElementShowState"),
            (0x06, focus_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
             "FocusIn(pid)"),
            (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
             "RegisterTsfNotifySink"),
            (0x14, lp("") + lp("") + lp("") + int(hwnd).to_bytes(8, "little") + lp("planb")
             + lp("python.exe"), "UpdateHostContext"),
            # the caret position - the engine may need it to treat the host as having a real
            # insert point (CursorPos: x, y, height, relative, wait)
            (0x08, pb_int(1, 100) + pb_int(2, 100) + pb_int(3, 20), "SetCursorPos")):
        res = pipe.call(op, body)
        print(f"[ctx] {name:22s} -> {res if res is None else res[2]}")

    player = play_async(wav)
    time.sleep(0.4)
    send_key(0xA5)
    print(f"[key] Right Alt down, playing {os.path.basename(wav)}")

    deadline = time.time() + hold
    seen = []
    while time.time() < deadline:
        pump(hwnd, 0.25, "hold")
        ops = (0x17, 0x0A, 0x19, 0x09) if "--poll-all" in sys.argv else (0x17,)
        for op in ops:
            res = pipe.call(op, b"")
            if res and res[1] and res[1] != b"\x00" * len(res[1]):
                seen.append((op, res[1]))
                print(f"   [op 0x{op:02X} {time.time() - deadline + hold:4.2f}s] "
                      f"{res[1].hex()} {res[1].decode('utf-8', 'replace')[:80]!r}")

    if "--stop-key" in sys.argv:
        # the engine neutralises the Alt state when the session starts, so the real key-up never
        # arrives; an unrelated key press during recording is what the hook maps to PRESS_STOP
        stopped = False
        for attempt in range(12):
            send_key(0x41)
            time.sleep(0.05)
            send_key(0x41, up=True)
            time.sleep(0.25)
            if os.path.exists(log_path):
                tail = open(log_path, "rb").read()[-20000:].decode("utf-8", "replace")
                if "controller handle msg=PRESS_STOP" in tail:
                    print(f"[key] unrelated key 'A' -> engine PRESS_STOP (attempt {attempt + 1})")
                    stopped = True
                    break
        if not stopped:
            print("[key] engine never reported PRESS_STOP after 12 attempts")
    send_key(0xA5, up=True)
    print("[key] Right Alt up; waiting for the tail")
    if "--click-stop" in sys.argv:
        send_click()
        print("[mouse] synthetic left click sent (engine stops voice on click)")
    if "--rpc-stop" in sys.argv:
        # the vendor's own core reports the key release to the engine over RPC; try the same
        keycode = pb_int(1, 0xA5) + pb_str(3, os.path.basename(sys.executable or "python.exe"))
        res = pipe.call(0x05, keycode)
        print(f"[rpc] KeyUp(0xA5) -> {res if res is None else res[2]}")
    for i in range(12):
        pump(hwnd, 0.4, "tail")
        res = pipe.call(0x17, b"")
        if res and res[1] and res[1] != b"\x00" * 12:
            seen.append(res[1])
            print(f"   [tail {i}] {res[1].hex()} {res[1].decode('utf-8','replace')[:80]!r}")
    if player:
        try:
            player.kill()
        except Exception:
            pass

    pipe.close()
    print("--- voice-related engine log ---")
    if os.path.exists(log_path):
        text = open(log_path, encoding="utf-8", errors="replace").read().splitlines()
        pat = re.compile(r"voice start|VoiceStop|voice session|asr|ASR|accepting_audio|wave|commit", re.I)
        for line in [l.strip() for l in text if l.strip() and pat.search(l)
                     and "samicore" not in l][-20:]:
            print("   " + line[:180])
    proc.terminate()
    print(f"[done] {len(seen)} non-empty peek response(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
