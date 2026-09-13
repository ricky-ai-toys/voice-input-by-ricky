"""DoubaoVoicePortable v2 - hold Right Alt, speak, text lands at the cursor.

v2 streams audio into the engine while you speak (the vendor's file test reads
the WAV progressively at roughly real time), so the cloud connection setup and
most of the recognition happen during your speech. Only the tail (VAD finish +
second pass) is left after you release the key.

Runs entirely in user space: no admin rights, no TSF registration, no changes to
system input methods.
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

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(APP_DIR)

SAMPLE_RATE = 16000
BYTES_PER_SEC = SAMPLE_RATE * 2
WAV_HEADER_BYTES = 44
MAX_SESSION_SECONDS = 55.0
WARM_FILE_SECONDS = 45.0      # pre-allocated silence for a pre-warmed session
WARM_MAX_AGE_SEC = 15.0       # recycle before the cloud drops the idle stream (~29s)
WARM_REFRESH_SEC = 2.0
LEAD_SEC = 0.25               # write this far ahead of the engine's read pointer
TAIL_SEC = 0.40               # silence kept after the utterance before EOF


def find_extract_script() -> str:
    candidates = [
        os.path.join(APP_DIR, "extract_text.js"),
        os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "extract_text.js"),
        os.path.join(os.path.dirname(APP_DIR), "app", "extract_text.js"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("extract_text.js not found in: " + ", ".join(candidates))


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def default_config() -> dict:
    return {
        "hotkey": "right alt",
        "suppress_hotkey": False,
        "record_sample_rate": SAMPLE_RATE,
        "min_duration_sec": 0.35,
        "runtime_dir": os.path.join("runtime", "v0.9.0.0"),
        "data_dir": "data",
        "restore_clipboard": True,
        "show_overlay": True,
        "paste_delay_ms": 60,
        "final_timeout_sec": 8.0,
        "language_hint": "auto",
    }


def resolve_paths(cfg: dict) -> dict:
    for key, sub in (("runtime_dir", os.path.join("runtime", "v0.9.0.0")), ("data_dir", "data")):
        value = str(cfg.get(key) or "")
        if not value:
            continue
        if not os.path.isabs(value):
            cfg[key] = os.path.join(DEFAULT_ROOT, value)
        elif not os.path.exists(value):
            cfg[key] = os.path.join(DEFAULT_ROOT, sub)
    return cfg


def load_config(path: str) -> dict:
    cfg = default_config()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return resolve_paths(cfg)


def save_config(path: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# clipboard / keystroke injection
# --------------------------------------------------------------------------- #
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

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
kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
kernel32.SetStdHandle.restype = wintypes.BOOL
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
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        try:
            ctypes.memmove(ptr, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
        return True
    finally:
        user32.CloseClipboard()


def send_ctrl_v() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, None)
    user32.keybd_event(VK_V, 0, 0, None)
    time.sleep(0.02)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, None)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, None)


def paste_text(text: str, cfg: dict) -> None:
    previous = get_clipboard_text() if cfg.get("restore_clipboard", True) else None
    if not set_clipboard_text(text):
        print("[warn] clipboard write failed", flush=True)
        return
    time.sleep(cfg.get("paste_delay_ms", 60) / 1000.0)
    send_ctrl_v()
    if cfg.get("restore_clipboard", True) and previous is not None:
        time.sleep(0.25)
        set_clipboard_text(previous)


# --------------------------------------------------------------------------- #
# audio sources: 16 kHz mono int16 chunks
# --------------------------------------------------------------------------- #
class MicSource:
    def __init__(self, sample_rate: int = SAMPLE_RATE):
        import sounddevice as sd

        self._sd = sd
        self.sample_rate = sample_rate
        self.chunks: queue.Queue[bytes] = queue.Queue()
        self._stream = None
        self._open()

    def _open(self) -> None:
        self._stream = self._sd.RawInputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16", callback=self._cb
        )

    def _cb(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self.chunks.put(bytes(indata), block=False)

    def start(self) -> None:
        self._drain()
        if self._stream is None:
            self._open()
        try:
            self._stream.start()
        except Exception:
            # a device change or a previous hard stop can leave the stream unusable
            self.close()
            self._open()
            self._stream.start()

    def stop(self) -> None:
        # NOTE: only pause here. Closing the PortAudio stream would make every
        # subsequent start() fail, which is what broke the second hotkey press.
        try:
            self._stream.stop()
        except Exception:
            pass
        self._drain()

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None

    def _drain(self) -> None:
        try:
            while True:
                self.chunks.get_nowait()
        except queue.Empty:
            pass


class FileSource:
    """Replays a wav as if it were live microphone audio (used for testing)."""

    def __init__(self, path: str):
        with wave.open(path, "rb") as fh:
            if fh.getframerate() != SAMPLE_RATE or fh.getnchannels() != 1:
                raise ValueError("simulation wav must be 16 kHz mono")
            self.chunks: queue.Queue[bytes] = queue.Queue()
            self._data = fh.readframes(fh.getnframes())
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        def pump() -> None:
            step = 1280  # 40 ms
            for i in range(0, len(self._data), step):
                self.chunks.put(self._data[i:i + step], block=False)
                time.sleep(0.04)

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# streaming ASR session against the vendor engine
# --------------------------------------------------------------------------- #
def _engine_pid_file() -> str:
    return os.path.join(DEFAULT_ROOT, "data", "engines.txt")


def remember_engine_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        path = _engine_pid_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{pid}\n")
    except Exception:
        pass


def forget_engine_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        path = _engine_pid_file()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            keep = [line for line in fh if line.strip().isdigit() and int(line) != pid]
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
    except Exception:
        pass


def reap_stale_engines() -> int:
    """Kill engine processes left behind by a previous (crashed) run."""
    path = _engine_pid_file()
    if not os.path.exists(path):
        return 0
    killed = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pids = {int(x) for x in fh.read().split() if x.strip().isdigit()}
        for pid in pids:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True,
                                 creationflags=0x08000000).stdout
            if str(pid) in out:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, creationflags=0x08000000)
                killed += 1
        os.remove(path)
    except Exception:
        pass
    return killed


class StreamSession:
    """Spawns the engine on a live-growing WAV and collects streamed results."""

    def __init__(self, exe: str, script_src: str, wav_path: str,
                 max_seconds: float = MAX_SESSION_SECONDS):
        self.exe = exe
        self.script_src = script_src
        self.wav_path = wav_path
        self.max_seconds = max_seconds
        self.interim = ""
        self.final = ""
        self.final_event = threading.Event()
        self._written = 0
        self._lock = threading.Lock()
        self._pid = None
        self._session = None
        self._script = None
        self._timings: dict[str, float] = {}
        self.reader_bytes = 0          # bytes the engine has actually consumed
        self.cursor = 0                # where the current utterance is written
        self._pending = b""            # stdout line buffer

    # -- lifecycle ---------------------------------------------------------- #
    def _prepare_wav(self) -> None:
        os.makedirs(os.path.dirname(self.wav_path), exist_ok=True)
        with wave.open(self.wav_path, "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(SAMPLE_RATE)
            fh.writeframes(b"\x00" * int(self.max_seconds * BYTES_PER_SEC))
        self._written = 0

    def start(self) -> None:
        import frida

        self._prepare_wav()
        t0 = time.time()
        argv = [self.exe, "--test-sami", "--wav", self.wav_path]
        self._pid = frida.spawn(self.exe, argv=argv, cwd=os.path.dirname(self.exe), stdio="inherit")
        self._session = frida.attach(self._pid)
        self._script = self._session.create_script(self.script_src)
        self._script.on("message", self._on_message)
        self._script.load()
        frida.resume(self._pid)
        remember_engine_pid(self._pid)
        self._timings["spawn_ms"] = (time.time() - t0) * 1000
        self._t0 = time.time()
        self._t_start = self._t0

    def live_edge_bytes(self) -> int:
        """Reader position, or a time-based fallback before the first frame log."""
        if self.reader_bytes:
            return self.reader_bytes
        elapsed = max(0.0, time.time() - self._t_start - 1.6)   # ~handshake before reading
        return int(elapsed * BYTES_PER_SEC)

    def begin_utterance(self, lead_sec: float = 0.25) -> int:
        """Start writing at the live edge (never behind the reader)."""
        self._t0 = time.time()          # utterance-relative timings from here on
        self._timings.pop("first_text_ms", None)
        self.cursor = self.live_edge_bytes() + int(lead_sec * BYTES_PER_SEC)
        return self.cursor

    def _on_message(self, message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        kind = payload.get("kind")
        if kind == "feed":
            self.reader_bytes += int(payload.get("bytes") or 0)
            return
        if kind != "json":
            return
        try:
            doc = json.loads(base64.b64decode(payload.get("b64") or "").decode("utf-8"))
        except Exception:
            return
        for item in doc.get("results") or []:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            if item.get("is_vad_finished"):
                self.final = text
                self._timings.setdefault("first_text_ms", (time.time() - self._t0) * 1000)
                self._timings["final_ms"] = (time.time() - self._t0) * 1000
                self.final_event.set()
            else:
                self.interim = text
                self._timings.setdefault("first_text_ms", (time.time() - self._t0) * 1000)

    # -- audio -------------------------------------------------------------- #
    def feed(self, pcm: bytes) -> None:
        with self._lock:
            if self.cursor + len(pcm) > int(self.max_seconds * BYTES_PER_SEC):
                return
            with open(self.wav_path, "r+b") as fh:
                fh.seek(WAV_HEADER_BYTES + self.cursor)
                fh.write(pcm)
            self.cursor += len(pcm)
            self._written += len(pcm)

    @property
    def written_seconds(self) -> float:
        return self._written / BYTES_PER_SEC

    def finished(self) -> bool:
        return self.final_event.is_set()

    def finish(self, timeout: float = 8.0) -> str:
        self.finalize_file()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.final_event.wait(0.1):
                break
            if not self.alive():
                break
        return self.final or self.interim

    def finalize_file(self) -> None:
        """Shrink the pre-allocated WAV to the audio we actually captured.

        The engine reads the declared length once at start; cutting the file at
        release makes the reader hit EOF, which is what ends the session quickly
        (otherwise it would keep reading the silence padding).
        """
        data_len = max(self.cursor, self.reader_bytes)
        try:
            with open(self.wav_path, "r+b") as fh:
                fh.seek(0)
                fh.write(_wav_header(data_len))
                fh.truncate(WAV_HEADER_BYTES + data_len)
            self._timings["finalize_ms"] = time.time() * 1000
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] finalize failed: {exc}", flush=True)

    def alive(self) -> bool:
        if self._pid is None:
            return False
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {self._pid}", "/NH"],
            capture_output=True, text=True, creationflags=0x08000000,
        ).stdout
        return str(self._pid) in out

    def close(self) -> None:
        try:
            if self._session:
                self._session.detach()
        except Exception:
            pass
        try:
            if self._pid is not None:
                import frida

                if self.alive():
                    frida.kill(self._pid)
        except Exception:
            pass
        forget_engine_pid(self._pid)
        self._pid = None

    @property
    def timings(self) -> dict:
        return dict(self._timings)


def _wav_header(data_len: int) -> bytes:
    import struct

    byte_rate = SAMPLE_RATE * 2
    return (
        b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, byte_rate, 2, 16)
        + b"data" + struct.pack("<I", data_len)
    )


# --------------------------------------------------------------------------- #
# overlay
# --------------------------------------------------------------------------- #
class Overlay:
    def __init__(self) -> None:
        import tkinter as tk

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        try:
            self.root.attributes("-toolwindow", True)
        except Exception:
            pass
        self.root.configure(bg="#111318")
        width, height = 560, 84
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+70")
        self.label = tk.Label(
            self.root, text="", fg="#f2f4f8", bg="#111318", justify="left", anchor="w",
            wraplength=width - 24, font=("Segoe UI", 10),
        )
        self.label.pack(expand=True, fill="both", padx=12, pady=8)
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
        self.root.after(50, self._pump)

    def show(self, text: str) -> None:
        self._queue.put(("show", text))

    def hide(self) -> None:
        self._queue.put(("hide", ""))

    def run(self) -> None:
        self.root.mainloop()


# --------------------------------------------------------------------------- #
# hotkey hook
# --------------------------------------------------------------------------- #
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_NAMES = {
    "right alt": 0xA5, "left alt": 0xA4, "right ctrl": 0xA3, "left ctrl": 0xA2,
    "right shift": 0xA1, "left shift": 0xA0, "caps lock": 0x14,
    "left win": 0x5B, "right win": 0x5C,
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL


class HotkeyHook:
    def __init__(self, vk: int, on_press, on_release, suppress: bool = False):
        self.vk, self.on_press, self.on_release, self.suppress = vk, on_press, on_release, suppress
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
                    if not self._down:
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
# application
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, cfg: dict, source=None):
        self.cfg = cfg
        self.exe = os.path.join(cfg["runtime_dir"], "ImeService.exe")
        if not os.path.exists(self.exe):
            raise FileNotFoundError(f"ImeService.exe not found under {cfg['runtime_dir']}")
        with open(find_extract_script(), encoding="utf-8") as fh:
            self.script_src = fh.read()
        self.data_dir = cfg["data_dir"]
        os.makedirs(self.data_dir, exist_ok=True)
        self.overlay = Overlay()
        self.show_overlay = bool(cfg.get("show_overlay", True))
        self.source = source or MicSource(cfg.get("record_sample_rate", SAMPLE_RATE))
        self.session: StreamSession | None = None
        self.busy = threading.Event()
        self.recording = False
        self._lock = threading.Lock()
        self._collected: list[str] = []
        self.last_text = ""
        self._generation = 0
        self._session_seq = 0
        self.warm: StreamSession | None = None
        self.warm_started_at = 0.0
        self._stopping = False

    # ---- streaming -------------------------------------------------------- #
    def on_press(self) -> None:
        with self._lock:
            if self.recording or self.busy.is_set():
                return
            self.recording = True
        self._collected = []
        try:
            self.source.start()
            self._open_session()
            with self._lock:
                self._generation += 1
                generation = self._generation
            threading.Thread(target=self._pump_audio, args=(generation,), daemon=True).start()
            if self.show_overlay:
                self.overlay.show("● Listening...  (release Right Alt to insert)")
        except Exception as exc:  # noqa: BLE001
            self.recording = False
            print(f"[error] start failed: {exc}", flush=True)
            if self.show_overlay:
                self.overlay.hide()

    def _open_session(self) -> None:
        """Take the pre-warmed session if one is ready, else create one now."""
        session = self.warm
        self.warm = None
        if session is None or session.finished() or not session.alive():
            if session is not None:
                session.close()
            session = self._new_session()
        self._session_seq += 1
        self.session = session
        self.session.begin_utterance(LEAD_SEC)
        print(f"[app] session {self._session_seq} started "
              f"(spawn {session.timings.get('spawn_ms', 0):.0f} ms, warm={'Y' if session.reader_bytes else 'N'}, "
              f"edge={session.live_edge_bytes()/BYTES_PER_SEC:.2f}s)", flush=True)

    def _new_session(self) -> StreamSession:
        wav_path = os.path.join(self.data_dir, f"live_{self._session_seq}_{int(time.time()*1000)}.wav")
        session = StreamSession(self.exe, self.server_script(), wav_path,
                                max_seconds=WARM_FILE_SECONDS)
        session.start()
        return session

    # ---- pre-warming ------------------------------------------------------ #
    def start_warm_now(self) -> None:
        """Create the first pre-warmed session right away (no waiting)."""
        try:
            session = self._new_session()
            self.warm = session
            self.warm_started_at = time.time()
            print(f"[app] pre-warmed a session (spawn {session.timings.get('spawn_ms', 0):.0f} ms)",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] initial warm-up failed: {exc}", flush=True)

    def _warm_worker(self) -> None:
        """Keep a connected-but-silent engine session ready between utterances."""
        self.start_warm_now()
        while not self._stopping:
            try:
                with self._lock:
                    busy = self.recording or self.busy.is_set()
                if busy:
                    time.sleep(WARM_REFRESH_SEC)
                    continue
                stale = (self.warm is None
                         or time.time() - self.warm_started_at > WARM_MAX_AGE_SEC
                         or self.warm.finished()
                         or not self.warm.alive())
                if not stale:
                    time.sleep(WARM_REFRESH_SEC)
                    continue
                old = self.warm
                self.warm = None
                if old is not None:
                    old.close()
                session = self._new_session()
                self.warm = session
                self.warm_started_at = time.time()
                print(f"[app] pre-warmed a session (spawn {session.timings.get('spawn_ms', 0):.0f} ms)",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] warm refresh failed: {exc}", flush=True)
            time.sleep(WARM_REFRESH_SEC)

    def server_script(self) -> str:
        return self.script_src

    def _pump_audio(self, generation: int) -> None:
        """Feed microphone chunks into the live WAV while the key is held."""
        last_ui = 0.0
        while True:
            with self._lock:
                if not self.recording or generation != self._generation:
                    break
            try:
                chunk = self.source.chunks.get(timeout=0.2)
            except queue.Empty:
                continue
            session = self.session
            if session is None:
                continue
            session.feed(chunk)
            # session ended early (VAD) while the user keeps talking -> chain a new one
            if session.finished():
                text = session.final or session.interim
                if text:
                    self._collected.append(text)
                session.close()
                try:
                    self._open_session()
                except Exception as exc:  # noqa: BLE001
                    print(f"[error] chaining session failed: {exc}", flush=True)
                    break
            now = time.time()
            if self.show_overlay and now - last_ui > 0.15:
                last_ui = now
                live = (session.interim or "").strip()
                self.overlay.show(
                    "● Listening...\n" + (live[-120:] if live else "(speak now, release to insert)")
                )

    def on_release(self) -> None:
        with self._lock:
            if not self.recording:
                return
            self.recording = False
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self) -> None:
        self.busy.set()
        t_release = time.time()
        try:
            self.source.stop()
            session = self.session
            text = ""
            if session is not None:
                if session.written_seconds < self.cfg.get("min_duration_sec", 0.35):
                    print(f"[app] too short ({session.written_seconds:.2f}s), ignored", flush=True)
                    session.close()
                    return
                if self.show_overlay:
                    self.overlay.show("… Transcribing")
                text = session.finish(self.cfg.get("final_timeout_sec", 8.0))
                timings = session.timings
                session.close()
                self.warm_started_at = 0.0     # force the warmer to build a fresh one
            else:
                timings = {}
            if self._collected and text and self._collected[-1] != text:
                self._collected.append(text)
            text = "".join(self._collected) if self._collected else text
            self.last_text = text
            elapsed = (time.time() - t_release) * 1000
            if not text:
                print(f"[app] no text (release->done {elapsed:.0f} ms) {timings}", flush=True)
                if self.show_overlay:
                    self.overlay.show("(nothing recognized)")
                    time.sleep(0.6)
                return
            print(f"[app] text={text!r} release->done={elapsed:.0f} ms "
                  f"first_text={timings.get('first_text_ms', 0):.0f} ms", flush=True)
            paste_text(text, self.cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", flush=True)
        finally:
            if self.show_overlay:
                self.overlay.hide()
            self.busy.clear()

    def run(self) -> None:
        hotkey = str(self.cfg["hotkey"]).lower()
        if hotkey not in VK_NAMES:
            raise SystemExit(f"unsupported hotkey '{hotkey}' (supported: {', '.join(VK_NAMES)})")
        hook = HotkeyHook(VK_NAMES[hotkey], self.on_press, self.on_release,
                          suppress=bool(self.cfg.get("suppress_hotkey", False)))
        print(f"[app] ready - hold '{hotkey}' to talk, release to insert text", flush=True)
        threading.Thread(target=self._warm_worker, daemon=True).start()
        if self.show_overlay:
            self.overlay.show("Ready - hold Right Alt and speak")
            self.overlay.root.after(1400, self.overlay.hide)
        self.overlay.run()
        hook.close()
        self.source.close()
        self._stopping = True
        if self.warm is not None:
            self.warm.close()


def simulate(cfg: dict, wav_path: str, paste: bool) -> int:
    """Run one streaming session from a wav file (no mic, no hotkey)."""
    app = App(cfg, source=FileSource(wav_path))
    app.start_warm_now()
    time.sleep(2.5)          # let the pre-warmed session start reading
    app.on_press()
    with wave.open(wav_path, "rb") as fh:
        duration = fh.getnframes() / fh.getframerate()
    deadline = time.time() + duration + 2
    while time.time() < deadline and app.recording:
        time.sleep(0.05)
        if app.session and app.session.written_seconds >= duration:
            break
    started = time.time()
    app.on_release()
    while app.busy.is_set():
        time.sleep(0.05)
    if app.overlay:
        pass
    text = app.last_text
    elapsed = (time.time() - started) * 1000
    print(f"[simulate] audio={duration:.2f}s release->text={elapsed:.0f} ms text={text!r}", flush=True)
    if paste and text:
        paste_text(text, cfg)
    return 0


# --------------------------------------------------------------------------- #
# start-with-windows (per-user, no admin)
# --------------------------------------------------------------------------- #
def startup_folder() -> str:
    return os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )


def autostart_path() -> str:
    return os.path.join(startup_folder(), "DoubaoVoice.cmd")


def install_autostart() -> int:
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
    else:
        exe = os.path.abspath(__file__)
    folder = startup_folder()
    if not os.path.isdir(folder):
        print(f"[error] startup folder not found: {folder}", file=sys.stderr)
        return 2
    cmd = f'@echo off\r\nstart "" "{exe}"\r\n'
    with open(autostart_path(), "w", encoding="utf-8", newline="") as fh:
        fh.write(cmd)
    print(f"[ok] autostart installed: {autostart_path()}")
    return 0


def remove_autostart() -> int:
    path = autostart_path()
    if os.path.exists(path):
        os.remove(path)
        print(f"[ok] autostart removed: {path}")
    else:
        print("[ok] autostart was not installed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DoubaoVoicePortable")
    ap.add_argument("--config", default=os.path.join(DEFAULT_ROOT, "data", "config.json"))
    ap.add_argument("--transcribe-file", help="legacy one-shot mode: transcribe a wav and exit")
    ap.add_argument("--simulate", help="run one streaming session from a wav (mic bypassed)")
    ap.add_argument("--paste", action="store_true", help="with --transcribe-file/--simulate, paste result")
    ap.add_argument("--install-autostart", action="store_true", help="start with Windows (per-user)")
    ap.add_argument("--remove-autostart", action="store_true", help="undo --install-autostart")
    args = ap.parse_args()

    if args.install_autostart:
        return install_autostart()
    if args.remove_autostart:
        return remove_autostart()

    if not os.path.exists(args.config):
        save_config(args.config, default_config())
    cfg = load_config(args.config)
    os.makedirs(cfg["data_dir"], exist_ok=True)

    if sys.stdout is None or sys.stderr is None:  # windowed build: log to file
        log_dir = os.path.join(cfg["data_dir"], "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "app.log")
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        # give the child engine a real stdout handle too: its own logging (which we
        # read the read-pointer from) is disabled when the handle is invalid.
        try:
            import msvcrt

            fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
            os.set_inheritable(fd, True)
            handle = msvcrt.get_osfhandle(fd)
            kernel32.SetStdHandle(-11, handle)   # STD_OUTPUT_HANDLE
            kernel32.SetStdHandle(-12, handle)   # STD_ERROR_HANDLE
        except Exception:
            pass
        if sys.stdout is None:
            sys.stdout = log_file
        if sys.stderr is None:
            sys.stderr = log_file

    if args.simulate:
        app_cfg = dict(cfg)
        app_cfg["show_overlay"] = False
        return simulate(app_cfg, args.simulate, args.paste)

    if args.transcribe_file:
        from doubao_voice_v2 import App as _App  # noqa: F401  (keeps legacy path available)

        app_cfg = dict(cfg)
        app_cfg["show_overlay"] = False
        return simulate(app_cfg, args.transcribe_file, args.paste)

    if not os.path.isdir(cfg["runtime_dir"]):
        print(f"[fatal] runtime dir not found: {cfg['runtime_dir']}", file=sys.stderr)
        return 2
    stale = reap_stale_engines()
    if stale:
        print(f"[app] cleaned up {stale} engine process(es) left by a previous run", flush=True)
    App(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
