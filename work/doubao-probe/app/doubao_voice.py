"""DoubaoVoicePortable - hold Right Alt, speak, text lands at the cursor.

Runs entirely in user space:
  * records 16 kHz mono PCM with sounddevice
  * feeds the WAV to the vendor's own ImeService.exe (--test-sami) under frida,
    applying the two patches needed for the standalone path and reading the
    unredacted result JSON
  * pastes the recognized text into the focused window via clipboard + Ctrl+V

No admin rights, no TSF registration, no system input-method changes.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import wave

# works both from source and from a PyInstaller bundle (app/ under the portable root)
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(APP_DIR)


def find_extract_script() -> str:
    """extract_text.js lives next to the source, or inside the frozen bundle."""
    candidates = [
        os.path.join(APP_DIR, "extract_text.js"),
        os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "extract_text.js"),
        os.path.join(os.path.dirname(APP_DIR), "app", "extract_text.js"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("extract_text.js not found (looked in: %s)" % ", ".join(candidates))


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def default_config() -> dict:
    return {
        "hotkey": "right alt",
        "suppress_hotkey": False,
        "sample_rate": 16000,
        "min_duration_sec": 0.4,
        # relative paths so the folder stays portable (resolved in resolve_paths)
        "runtime_dir": os.path.join("runtime", "v0.9.0.0"),
        "data_dir": "data",
        "injection": "paste",
        "restore_clipboard": True,
        "show_overlay": True,
        "language": "zh",
        "paste_delay_ms": 60,
        "asr_timeout_sec": 60,
    }


def load_config(path: str) -> dict:
    cfg = default_config()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return resolve_paths(cfg)


def resolve_paths(cfg: dict) -> dict:
    """Make runtime_dir/data_dir point at the copy that lives next to this app."""
    for key in ("runtime_dir", "data_dir"):
        value = str(cfg.get(key) or "")
        if not value:
            continue
        if not os.path.isabs(value):
            cfg[key] = os.path.join(DEFAULT_ROOT, value)
        elif not os.path.exists(value):
            # config was copied from another machine/folder - retry relative
            tail = value.split(os.sep)[-1]
            candidate = os.path.join(DEFAULT_ROOT, "runtime", tail) if key == "runtime_dir" else os.path.join(DEFAULT_ROOT, "data")
            if os.path.exists(candidate):
                cfg[key] = candidate
    return cfg


def save_config(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# clipboard + keystroke injection
# --------------------------------------------------------------------------- #
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64-bit safe prototypes: without these ctypes truncates handles to 32 bits.
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL

user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
user32.keybd_event.restype = None


def _open_clipboard(retries: int = 12) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        size = len(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        try:
            ctypes.memmove(ptr, data, size)
        finally:
            kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
        return True
    finally:
        user32.CloseClipboard()


VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


def send_ctrl_v() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, None)
    user32.keybd_event(VK_V, 0, 0, None)
    time.sleep(0.02)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, None)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, None)


def paste_text(text: str, cfg: dict) -> None:
    previous = get_clipboard_text() if cfg.get("restore_clipboard", True) else None
    if not set_clipboard_text(text):
        print("[warn] clipboard write failed; text kept in console only")
        return
    time.sleep(cfg.get("paste_delay_ms", 60) / 1000.0)
    send_ctrl_v()
    if cfg.get("restore_clipboard", True) and previous is not None:
        time.sleep(0.25)
        set_clipboard_text(previous)


# --------------------------------------------------------------------------- #
# audio capture
# --------------------------------------------------------------------------- #
class Recorder:
    def __init__(self, sample_rate: int):
        import sounddevice as sd  # imported lazily so --help works without it

        self._sd = sd
        self.sample_rate = sample_rate
        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            callback=self._on_audio,
        )
        self._stream.start()
        self._capturing = False
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self.started_at = 0.0

    def begin(self) -> None:
        with self._lock:
            if self._capturing:
                return
            self._frames = []
            self._capturing = True
            self.started_at = time.time()

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        with self._lock:
            if self._capturing:
                self._frames.append(bytes(indata))

    def end(self) -> tuple[bytes | None, float]:
        with self._lock:
            if not self._capturing:
                return None, 0.0
            frames = self._frames
            self._capturing = False
            self._frames = []
        duration = time.time() - self.started_at
        if not frames:
            return None, duration
        return b"".join(frames), duration

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


def write_wav(path: str, pcm: bytes, sample_rate: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm)


# --------------------------------------------------------------------------- #
# ASR through the vendor's own service
# --------------------------------------------------------------------------- #
class DoubaoAsr:
    def __init__(self, runtime_dir: str, script_path: str, timeout: int = 60):
        self.exe = os.path.join(runtime_dir, "ImeService.exe")
        self.script_path = script_path
        self.timeout = timeout
        if not os.path.exists(self.exe):
            raise FileNotFoundError(f"ImeService.exe not found under {runtime_dir}")

    def transcribe(self, wav_path: str) -> str:
        import frida

        with open(self.script_path, "r", encoding="utf-8") as fh:
            source = fh.read()

        argv = [self.exe, "--test-sami", "--wav", wav_path]
        pid = frida.spawn(self.exe, argv=argv, cwd=os.path.dirname(self.exe), stdio="inherit")
        session = frida.attach(pid)
        script = session.create_script(source)

        state = {"final": "", "last": ""}
        done = threading.Event()

        def on_message(message, data):  # noqa: ANN001
            if message.get("type") != "send":
                return
            payload = message.get("payload") or {}
            if payload.get("kind") == "json":
                raw = payload.get("b64") or ""
                try:
                    doc = json.loads(base64.b64decode(raw).decode("utf-8"))
                except Exception:
                    return
                for item in doc.get("results") or []:
                    text = (item.get("text") or "").strip()
                    if not text:
                        continue
                    state["last"] = text
                    if item.get("is_vad_finished"):
                        state["final"] = text
                        done.set()

        script.on("message", on_message)
        script.load()
        frida.resume(pid)

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if done.wait(0.2):
                break
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            ).stdout
            if str(pid) not in out:
                break

        try:
            session.detach()
        except Exception:
            pass
        try:
            if not done.is_set():
                frida.kill(pid)
        except Exception:
            pass
        return state["final"] or state["last"]


# --------------------------------------------------------------------------- #
# low level keyboard hook (own implementation: no third party hotkey library)
# --------------------------------------------------------------------------- #
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_NAMES = {
    "right alt": 0xA5,
    "left alt": 0xA4,
    "right ctrl": 0xA3,
    "left ctrl": 0xA2,
    "right shift": 0xA1,
    "left shift": 0xA0,
    "caps lock": 0x14,
    "right win": 0x5C,
    "left win": 0x5B,
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class HotkeyHook:
    """Global low-level keyboard hook with press/hold/release semantics."""

    def __init__(self, vk: int, on_press, on_release, suppress: bool = False):
        self.vk = vk
        self.on_press = on_press
        self.on_release = on_release
        self.suppress = suppress
        self._down = False
        self._proc = HOOKPROC(self._callback)
        self._handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def _callback(self, code, wparam, lparam):  # noqa: ANN001
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.vkCode == self.vk:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if not self._down:          # ignore auto-repeat
                        self._down = True
                        try:
                            self.on_press()
                        except Exception as exc:  # noqa: BLE001
                            print(f"[error] hotkey press: {exc}", flush=True)
                    if self.suppress:
                        return 1
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    self._down = False
                    try:
                        self.on_release()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[error] hotkey release: {exc}", flush=True)
                    if self.suppress:
                        return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def close(self) -> None:
        if self._handle:
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None


# --------------------------------------------------------------------------- #
# overlay
# --------------------------------------------------------------------------- #
class Overlay:
    def __init__(self) -> None:
        import tkinter as tk

        self._tk = tk
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        try:
            self.root.attributes("-toolwindow", True)
        except Exception:
            pass
        self.root.configure(bg="#1f1f1f")
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"260x64+{screen_w // 2 - 130}+80")
        self.label = tk.Label(
            self.root, text="", fg="#ffffff", bg="#1f1f1f",
            font=("Microsoft YaHei UI", 11),
        )
        self.label.pack(expand=True, fill="both")
        self.root.withdraw()
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._pump()

    def _pump(self) -> None:
        try:
            while True:
                action, text = self._queue.get_nowait()
                if action == "show":
                    self.label.config(text=text)
                    self.root.deiconify()
                else:
                    self.root.withdraw()
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def show(self, text: str) -> None:
        self._queue.put(("show", text))

    def hide(self) -> None:
        self._queue.put(("hide", ""))

    def run(self) -> None:
        self.root.mainloop()


# --------------------------------------------------------------------------- #
# application
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, cfg: dict, cfg_path: str):
        self.cfg = cfg
        self.cfg_path = cfg_path
        data_dir = cfg["data_dir"]
        os.makedirs(data_dir, exist_ok=True)
        self.tmp_wav = os.path.join(data_dir, "last_recording.wav")
        script_path = find_extract_script()
        self.asr = DoubaoAsr(cfg["runtime_dir"], script_path, cfg.get("asr_timeout_sec", 60))
        self.recorder = Recorder(cfg["sample_rate"])
        self.overlay = Overlay()
        self.show_overlay = bool(cfg.get("show_overlay", True))
        self.busy = threading.Event()
        self.recording = False
        self._lock = threading.Lock()

    # ---- hotkey handlers -------------------------------------------------- #
    def on_press(self) -> None:
        with self._lock:
            if self.recording or self.busy.is_set():
                return
            self.recording = True
        try:
            self.recorder.begin()
            print("[app] recording…", flush=True)
            if self.show_overlay:
                self.overlay.show("🎤 正在听…（松开结束）")
        except Exception as exc:  # noqa: BLE001
            self.recording = False
            print(f"[error] cannot start recording: {exc}", flush=True)
            if self.show_overlay:
                self.overlay.hide()

    def on_release(self) -> None:
        with self._lock:
            if not self.recording:
                return
            self.recording = False
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self) -> None:
        self.busy.set()
        try:
            pcm, duration = self.recorder.end()
            if pcm is None or duration < self.cfg.get("min_duration_sec", 0.4):
                print(f"[app] too short ({duration:.2f}s), ignored", flush=True)
                return
            write_wav(self.tmp_wav, pcm, self.cfg["sample_rate"])
            if self.show_overlay:
                self.overlay.show("⏳ 识别中…")
            started = time.time()
            text = self.asr.transcribe(self.tmp_wav)
            elapsed = time.time() - started
            if not text:
                print(f"[app] no text ({elapsed:.1f}s)", flush=True)
                if self.show_overlay:
                    self.overlay.show("（没听清）")
                    time.sleep(0.7)
                return
            print(f"[app] {text}  ({elapsed:.1f}s)", flush=True)
            paste_text(text, self.cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", flush=True)
        finally:
            if self.show_overlay:
                self.overlay.hide()
            self.busy.clear()

    # ---- run -------------------------------------------------------------- #
    def run(self) -> None:
        hotkey = str(self.cfg["hotkey"]).lower()
        if hotkey not in VK_NAMES:
            raise SystemExit(f"unsupported hotkey '{hotkey}' (supported: {', '.join(VK_NAMES)})")
        hook = HotkeyHook(
            VK_NAMES[hotkey],
            self.on_press,
            self.on_release,
            suppress=bool(self.cfg.get("suppress_hotkey", False)),
        )
        self.hook = hook
        print(f"[app] ready - hold '{hotkey}' to talk, release to insert text", flush=True)
        if self.show_overlay:
            self.overlay.show("✅ 就绪：按住右 Alt 说话")
            self.overlay.root.after(1200, self.overlay.hide)
        self.overlay.run()   # tk message loop is what drives the LL keyboard hook
        hook.close()
        self.recorder.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="DoubaoVoicePortable")
    ap.add_argument("--config", default=os.path.join(DEFAULT_ROOT, "data", "config.json"))
    ap.add_argument("--transcribe-file", help="run the ASR on a wav once and exit")
    ap.add_argument("--paste", action="store_true", help="with --transcribe-file, paste the result")
    args = ap.parse_args()

    if not os.path.exists(args.config):
        save_config(args.config, default_config())   # keep relative paths on disk
    cfg = load_config(args.config)
    os.makedirs(cfg["data_dir"], exist_ok=True)

    # windowed (no-console) builds: keep a log file instead of stdout
    if sys.stdout is None or sys.stderr is None:
        log_dir = os.path.join(cfg["data_dir"], "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = open(os.path.join(log_dir, "app.log"), "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_file
        if sys.stderr is None:
            sys.stderr = log_file

    if args.transcribe_file:
        script = find_extract_script()
        asr = DoubaoAsr(cfg["runtime_dir"], script, cfg.get("asr_timeout_sec", 60))
        text = asr.transcribe(args.transcribe_file)
        print(text)
        if args.paste and text:
            paste_text(text, cfg)
        return 0

    if not os.path.isdir(cfg["runtime_dir"]):
        print(f"[fatal] runtime dir not found: {cfg['runtime_dir']}", file=sys.stderr)
        return 2
    if not os.path.exists(os.path.join(cfg["runtime_dir"], "ImeService.exe")):
        print("[fatal] ImeService.exe missing (did you patch the manifest?)", file=sys.stderr)
        return 2

    App(cfg, args.config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
