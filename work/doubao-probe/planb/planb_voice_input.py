"""Plan B prototype - hold Right Alt, speak, text lands at the cursor (user level, no admin).

The transcript is not delivered to a client the engine does not know, but it *is* readable from
the pipe while a session records: `GetInlineCommitText` (op 0x19) and `GetCompText` (op 0x09)
return the sentence the cloud ASR has recognised so far (protobuf: field 1 = UTF-8 text,
field 2 = caret). This tool:

1. starts the bundled engine copy as a server (uiAccess patched, private pipe names),
2. arms the engine's own settings channel (`setVoiceTryoutActive`) and registers this process
   as the host (focus owner = current foreground pid, sink = our hidden window),
3. watches the real Right Alt key with a low-level hook - **no key injection**, so nothing can
   get stuck - and while the key is held it polls op 0x19/0x09 and keeps the newest text,
4. on release, pastes the result at the cursor (clipboard + Ctrl+V, like the shipping tool).

    python planb_voice_input.py scratch              # use it for real (hold Right Alt)
    python planb_voice_input.py scratch --self-test  # inject the key + play a wav, print text
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

from pipe_client import Pipe, pb_int, pb_str, start_server
from release_stuck_keys import RELEASE, down_keys
from settings_ipc_client import SettingsPipe, request
from try_voice import make_foreground_window, send_key
from tsf_activate import activate_doubao_for_process

FFPLAY = r"E:\ffmpeg\bin\ffplay.exe"
PIPE_NAME = "\\\\.\\pipe\\ObricIme\\oime-serve1"
DEFAULT_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "audio", "three_zh_16k.wav")
VK_RMENU = 0xA5


def load_commit_client(runtime: str):
    """The vendor's own commit API, from the copy we ship.

    `RpcPipe_PeekVoiceCommitUtf8(pipe, &session, buf, size)` hands over the final transcript and
    `RpcPipe_AckVoiceCommit(pipe, session)` clears it. The ack is not optional: until it happens
    the engine refuses to start another recording ("voice record start blocked by unacked commit").
    """
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))
    peek = dll.RpcPipe_PeekVoiceCommitUtf8
    peek.restype = ctypes.c_int
    peek.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_char_p,
                     ctypes.c_int]
    ack = dll.RpcPipe_AckVoiceCommit
    ack.restype = ctypes.c_int
    ack.argtypes = [ctypes.c_char_p, ctypes.c_uint64]
    return peek, ack


def paste_text(text: str, target_window: int = 0) -> None:
    """Put `text` on the clipboard and send Ctrl+V, optionally refocusing `target_window`."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    if target_window:
        user32.SetForegroundWindow(target_window)
        time.sleep(0.05)
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    raw = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, raw, len(raw))
    kernel32.GlobalUnlock(handle)
    user32.OpenClipboard(None)
    user32.EmptyClipboard()
    user32.SetClipboardData(CF_UNICODETEXT, handle)
    user32.CloseClipboard()
    time.sleep(0.05)
    VK_CONTROL, VK_V = 0x11, 0x56
    user32.keybd_event(VK_CONTROL, 0, 0, None)
    user32.keybd_event(VK_V, 0, 0, None)
    user32.keybd_event(VK_V, 0, 2, None)
    user32.keybd_event(VK_CONTROL, 0, 2, None)

# For scripted self-tests only: the shipped copy keeps the vendor's injected-key filters (a real
# hotkey is not injected). The self-test relaxes them in the engine's memory and puts the
# original bytes back afterwards, so the file on disk is never modified.
SELFTEST_PATCHES = [
    (0x742743, bytes.fromhex("41f64008107511"), b"\x90" * 7),
    (0x742DBD, bytes.fromhex("f64708100f8513010000"), bytes.fromhex("f6470810") + b"\x90" * 6),
    (0x74396F, bytes.fromhex("41f6400c010f8542020000"), bytes.fromhex("41f6400c01") + b"\x90" * 6),
    (0x750D50, bytes.fromhex("ff1502b68900"), bytes.fromhex("31c090909090")),
]

FRIDA_JS = r"""
const exe = Process.getModuleByName('ImeService.exe');
rpc.exports = {
  read(off, len) { return Array.from(new Uint8Array(exe.base.add(off).readByteArray(len))); },
  write(off, bytes) {
    const addr = exe.base.add(off);
    const size = bytes.length;
    Memory.protect(addr, size, 'rwx');      // .text is read-execute by default
    addr.writeByteArray(bytes);
    Memory.protect(addr, size, 'r-x');
    return true;
  }
};
send({ kind: 'ready' });
"""


def relax_engine_filters(pid: int):
    """Patch the injected-key filters in the engine's memory; returns session + originals."""
    import frida          # dev-only: the shipped product uses the real hotkey and needs no hook

    session = frida.attach(pid)
    script = session.create_script(FRIDA_JS)
    ready = {"ok": False}
    script.on("message", lambda m, d: ready.update(ok=True) if m.get("type") == "send" else None)
    script.load()
    while not ready["ok"]:
        time.sleep(0.05)
    originals = []
    for offset, expected, patch in SELFTEST_PATCHES:
        current = bytes(script.exports_sync.read(offset, len(expected)))
        if current != expected:
            print(f"[self-test] 0x{offset:X} unexpected bytes {current.hex()}, skipped")
            continue
        script.exports_sync.write(offset, list(patch))
        originals.append((offset, expected))
    print(f"[self-test] relaxed {len(originals)} filter(s) in the engine's memory")
    return script, originals


def restore_engine_filters(script, originals) -> None:
    for offset, original in originals:
        script.exports_sync.write(offset, list(original))
    print(f"[self-test] restored {len(originals)} filter(s)")
    try:
        script.unload()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# protobuf helpers for the two text ops
# --------------------------------------------------------------------------- #
def pb_first_string(body: bytes) -> str:
    """Extract field 1 (string) from a protobuf body."""
    pos = 0
    while pos < len(body):
        key = 0
        shift = 0
        while True:
            byte = body[pos]
            pos += 1
            key |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        field, wire = key >> 3, key & 7
        if wire == 2:
            size = 0
            shift = 0
            while True:
                byte = body[pos]
                pos += 1
                size |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            raw = body[pos:pos + size]
            pos += size
            if field == 1:
                return raw.decode("utf-8", "replace")
        elif wire == 0:
            while body[pos] & 0x80:
                pos += 1
            pos += 1
        else:
            return ""
    return ""


class PlanBVoice:
    """Resident voice pipe: trigger by hotkey, read the text from the engine, paste it."""

    def __init__(self, runtime: str, paste: bool = True, verbose: bool = True,
                 use_tsf: bool = True, proc=None) -> None:
        self.runtime = runtime
        self.paste = paste
        self.verbose = verbose
        self.use_tsf = use_tsf
        # `proc` lets a caller bring the engine up itself (e.g. frida spawn-suspend auditing)
        self.proc = proc if proc is not None else start_server(runtime)
        self.pipe = Pipe(timeout=35.0)
        self.hwnd = make_foreground_window(activate=False)
        self.text = ""
        self.session_text = ""
        self.target_window = 0
        self.polling = threading.Event()
        self.tcs: threading.Thread | None = None
        self.armed = False
        self.commit_client = None
        self.commit_timeout = 2.5

    # ---- engine side ------------------------------------------------------ #
    def arm(self) -> None:
        sp = SettingsPipe()
        sp.call(request("settings.setVoiceTryoutActive",
                        {"active": True, "cookie": int(time.time())}))
        sp.close()
        if self.use_tsf:
            activate_doubao_for_process(verbose=False)
        my_pid = os.getpid()
        for op, body in ((0x02, b""),
                         (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb")),
                         (0x1B, bytes.fromhex("0801")),
                         (0x0C, bytes.fromhex("08f8071801")),
                         (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little")
                          + pb_str(3, "python.exe")),
                         (0x15, int(self.hwnd).to_bytes(8, "little")
                          + (0x1122334455667788).to_bytes(8, "little")),
                         (0x08, pb_int(1, 100) + pb_int(2, 100) + pb_int(3, 20))):
            self.pipe.call(op, body)
        self.armed = True

    def refresh_focus(self, pid: int) -> None:
        body = pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + pb_str(3, "python.exe")
        self.pipe.call(0x06, body)

    def update_context(self) -> None:
        # before/after text, tick, reason, app
        payload = (len(b"").to_bytes(4, "little") + len(b"").to_bytes(4, "little")
                   + len(b"").to_bytes(4, "little")
                   + int(self.hwnd).to_bytes(8, "little"))
        payload += (5).to_bytes(4, "little") + b"planb"
        payload += (10).to_bytes(4, "little") + b"python.exe"
        self.pipe.call(0x14, payload)

    def read_text(self) -> str:
        best = ""
        for op in (0x19, 0x09):
            res = self.pipe.call(op, b"", timeout=0.5)
            if res and res[1]:
                text = pb_first_string(res[1])
                if len(text) > len(best):
                    best = text
        return best

    # ---- hotkey ----------------------------------------------------------- #
    def on_press(self) -> None:
        fg = ctypes.windll.user32.GetForegroundWindow()
        self.target_window = fg
        pid = wt.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        self.refresh_focus(pid.value or os.getpid())
        self.update_context()
        self.session_text = ""
        self.text = ""
        self.polling.set()
        self.tcs = threading.Thread(target=self._poll_loop, daemon=True)
        self.tcs.start()
        if self.verbose:
            print("[planb] listening ...", flush=True)

    def _poll_loop(self) -> None:
        while self.polling.is_set():
            try:
                text = self.read_text()
            except Exception:
                text = ""
            if text and len(text) >= len(self.session_text):
                self.session_text = text
                if self.verbose and text != self.text:
                    print(f"[planb] {text}", flush=True)
                self.text = text
            time.sleep(0.06)

    # ---- commit (final text) ---------------------------------------------- #
    def _commit_api(self):
        if self.commit_client is None:
            self.commit_client = load_commit_client(self.runtime)
        return self.commit_client

    def wait_commit(self) -> tuple[str, int]:
        """Poll the engine's commit until the final transcript shows up."""
        peek, _ack = self._commit_api()
        buf = ctypes.create_string_buffer(0x40001)
        session = ctypes.c_uint64(0)
        deadline = time.time() + self.commit_timeout
        while True:
            count = peek(PIPE_NAME.encode(), ctypes.byref(session), buf, 0x40001)
            if count or session.value:
                return buf.value.decode("utf-8", "replace"), session.value
            if time.time() > deadline:
                return "", 0
            time.sleep(0.08)

    def ack_commit(self, session: int) -> None:
        _peek, ack = self._commit_api()
        ack(PIPE_NAME.encode(), session)

    def on_release(self) -> None:
        self.polling.clear()
        if self.tcs:
            self.tcs.join(timeout=1.0)
        # the engine finalises the sentence shortly after the key goes up; its commit carries the
        # authoritative text (and must be acked or the next session is refused)
        started = time.time()
        text, session = self.wait_commit()
        waited = time.time() - started
        if session:
            self.ack_commit(session)
        if not text:
            text = self.text or self.session_text
        self.text = text
        if not text:
            if self.verbose:
                print("[planb] (nothing recognised)", flush=True)
            return
        if self.verbose:
            print(f"[planb] final ({waited:.2f}s after release): {text}", flush=True)
        if self.paste:
            paste_text(text, self.target_window)

    # ---- lifecycle -------------------------------------------------------- #
    def close(self) -> None:
        self.polling.clear()
        try:
            self.pipe.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
        except Exception:
            pass
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong,
                                       ctypes.c_void_p]
        for vk, flags in RELEASE:
            user32.keybd_event(vk, 0, flags, None)
        held = down_keys()
        if held:
            print(f"[safety] keys still held: {[hex(v) for v in held]}")


def run_self_test(voice: PlanBVoice, wav: str, hold: float, repeat: int = 1) -> int:
    """Stand-in for the user: inject Right Alt and play a wav, then report the text."""
    script, originals = relax_engine_filters(voice.proc.pid)
    results = []
    try:
        for i in range(repeat):
            player = None
            if os.path.exists(FFPLAY) and os.path.exists(wav):
                player = subprocess.Popen(
                    [FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", wav],
                    creationflags=0x00000008)
            time.sleep(0.3)
            voice.refresh_focus(os.getpid())
            voice.on_press()
            send_key(VK_RMENU)
            time.sleep(hold)
            send_key(VK_RMENU, up=True)
            voice.on_release()
            if player:
                try:
                    player.kill()
                except Exception:
                    pass
            results.append(voice.text)
            print(f"[self-test] session {i + 1}/{repeat} -> {voice.text!r}", flush=True)
            time.sleep(1.0)
    finally:
        restore_engine_filters(script, originals)
    return 0 if all(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Plan B voice pipe (prototype)")
    ap.add_argument("runtime", nargs="?", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "scratch"))
    ap.add_argument("--self-test", action="store_true",
                    help="inject the hotkey and play a wav instead of waiting for a real key")
    ap.add_argument("--wav", default=DEFAULT_WAV)
    ap.add_argument("--hold", type=float, default=7.0)
    ap.add_argument("--repeat", type=int, default=1, help="self-test: run N back-to-back sessions")
    ap.add_argument("--no-paste", action="store_true")
    ap.add_argument("--no-tsf", action="store_true",
                    help="do not activate the installed TSF profile (proves the pipe alone works)")
    args = ap.parse_args()

    voice = PlanBVoice(os.path.abspath(args.runtime), paste=not args.no_paste,
                       use_tsf=not args.no_tsf)
    print(f"[planb] engine pid={voice.proc.pid}, host window=0x{voice.hwnd:X}")
    voice.arm()
    print("[planb] armed (voice tryout + host context)")
    try:
        if args.self_test:
            return run_self_test(voice, args.wav, args.hold, args.repeat)
        print("[planb] hold Right Alt and speak - Ctrl+C to quit")
        # a low-level hook so the engine sees the *real* key; we only observe it here
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        class KBD(ctypes.Structure):
            _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                        ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

        @HOOKPROC
        def proc(ncode, wparam, lparam):  # noqa: ANN001
            if ncode == 0:
                info = ctypes.cast(lparam, ctypes.POINTER(KBD)).contents
                if info.vkCode == VK_RMENU:
                    if wparam == 0x0104:
                        threading.Thread(target=voice.on_press, daemon=True).start()
                    elif wparam == 0x0105:
                        threading.Thread(target=voice.on_release, daemon=True).start()
            return user32.CallNextHookEx(None, ncode, wparam, lparam)

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
        user32.SetWindowsHookExW.restype = wt.HHOOK
        hook = user32.SetWindowsHookExW(13, proc, None, 0)
        print(f"[planb] keyboard hook = {hook}")
        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                        ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]
        msg = MSG()
        while True:
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n[planb] stopped")
    finally:
        voice.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
