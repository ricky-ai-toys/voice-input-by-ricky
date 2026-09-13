"""Voice Input by Ricky - portable voice typing that needs no admin rights and no install.

Hold Right Alt, speak, release: the text is pasted at the caret. The engine is the seller's own
binary, running from the `runtime` folder next to this program; the shell only drives its private
local pipe, so nothing is registered with the system and no host TSF core is involved.

Layout (portable, any folder, no installer):

    VoiceInputByRicky/
      VoiceInputByRicky.exe      this program
      runtime/                   the engine copy (ImeService.exe + its DLLs)
      data/config.json           settings, created on first run

    VoiceInputByRicky.exe [--runtime DIR] [--hotkey "right alt"]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import queue
import sys
import threading
import time

import audio_endpoints
from planb_voice_input import (PIPE_NAME, PlanBVoice, force_foreground, window_pid,
                               window_title)
from release_stuck_keys import RELEASE

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
VK_NAMES = {
    "right alt": 0xA5, "left alt": 0xA4, "right ctrl": 0xA3, "left ctrl": 0xA2,
    "right shift": 0xA1, "left shift": 0xA0, "caps lock": 0x14,
    "left win": 0x5B, "right win": 0x5C,
}
APP_NAME = "Voice Input by Ricky"


def _paths() -> dict:
    base = app_dir()
    return {"base": base, "data": os.path.join(base, "data"),
            "logs": os.path.join(base, "data", "logs"),
            "config": os.path.join(base, "data", "config.json"),
            "runtime": os.path.join(base, "runtime")}


def engine_config_path() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "DoubaoIme", "conf", "config.json")


TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def kill_orphan_engines(runtime: str, log: SessionLog) -> int:
    """Kill engine processes left behind by an earlier run of *this* folder.

    A leftover engine still owns the private pipe and may hold a half-finished session or an
    unacknowledged commit, which is exactly the state that makes every later dictation fail.
    Only processes whose executable lives in our own runtime folder are touched.
    """
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    want = os.path.normcase(os.path.join(runtime, "ImeService.exe"))
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    killed = 0
    if snapshot == wintypes.HANDLE(-1).value:
        return 0
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return 0
        while True:
            if entry.szExeFile.lower() == "imeservice.exe":
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False,
                    entry.th32ProcessID)
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        path = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(handle, 0, path,
                                                               ctypes.byref(size)):
                            if os.path.normcase(path.value) == want:
                                if kernel32.TerminateProcess(handle, 0):
                                    killed += 1
                                    log.write(f"stopped a leftover engine (pid "
                                              f"{entry.th32ProcessID}) from an earlier run")
                    finally:
                        kernel32.CloseHandle(handle)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    if killed:
        # the pipe and the single-instance mutex outlive TerminateProcess for a moment; a new
        # engine started too early sees "Server Already Exists" and exits on us
        if not wait_pipe_gone(PIPE_NAME):
            log.write("warning: the private pipe is still busy after stopping the leftovers")
    return killed


def wait_pipe_gone(name: str, timeout: float = 6.0) -> bool:
    GENERIC_READ, GENERIC_WRITE, OPEN_EXISTING = 0x80000000, 0x40000000, 3
    kernel32.CreateFileW.restype = wintypes.HANDLE
    deadline = time.time() + timeout
    while time.time() < deadline:
        handle = kernel32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None,
                                      OPEN_EXISTING, 0, None)
        if handle in (None, 0, wintypes.HANDLE(-1).value):
            return True
        kernel32.CloseHandle(handle)
        time.sleep(0.1)
    return False


def single_instance_guard() -> int | None:
    """Returns a mutex handle to keep, or None when another copy is already running."""
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, False, "VoiceInputByRickyApp")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle


class SessionLog:
    """Every session leaves a trace - this is the only way to debug a machine we cannot touch."""

    def __init__(self, path: str, echo: bool) -> None:
        self.echo = echo
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.fh = open(path, "a", encoding="utf-8")
        self.write(f"--- {APP_NAME} started (pid {os.getpid()}) ---")

    def write(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        try:
            self.fh.write(line + "\n")
            self.fh.flush()
        except OSError:
            pass
        if self.echo:
            print(f"[{APP_NAME}] {message}", flush=True)

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass


def app_dir() -> str:
    """Folder that holds the exe (frozen) or this script (source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_config() -> dict:
    return {"hotkey": "right alt", "show_overlay": True, "paste": True, "commit_timeout": 2.5,
            "microphone": ""}      # empty = follow the Windows default recording device


def load_config(path: str) -> dict:
    cfg = default_config()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        except (OSError, ValueError) as exc:
            print(f"[config] {path}: {exc}; using defaults", flush=True)
    else:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
        except OSError as exc:
            print(f"[config] cannot write {path}: {exc}", flush=True)
    return cfg


def set_engine_microphone(mic_id: str, log: SessionLog) -> tuple[str, str, list[str]]:
    """Make sure the engine records from a device that exists.

    A fresh machine has `voice.selectedMicrophoneId` empty; the engine then picks an endpoint of
    its own and can end up recording silence (or refusing to start). We fill in the endpoint
    Windows calls the default, unless the config already names an active one.
    """
    try:
        default = audio_endpoints.default_capture_id()
        active = [endpoint for endpoint, _state in audio_endpoints.active_capture_ids()]
    except OSError as exc:
        log.write(f"microphone: cannot enumerate devices ({exc})")
        return mic_id, "", []
    log.write(f"microphone: default={default or '(none)'} active={len(active)} "
              f"requested={mic_id or '(auto)'}")
    wanted = mic_id or default
    if not wanted:
        log.write("microphone: no recording device is available")
        return "", default, active
    if active and wanted not in active:
        log.write(f"microphone: {wanted} is not active, falling back to the default")
        wanted = default
    path = engine_config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        log.write(f"microphone: cannot read {path} ({exc})")
        return wanted, default, active
    current = (cfg.get("voice") or {}).get("selectedMicrophoneId") or ""
    if current == wanted:
        log.write(f"microphone: engine already uses {wanted}")
        return wanted, default, active
    cfg.setdefault("voice", {})["selectedMicrophoneId"] = wanted
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        log.write(f"microphone: engine config updated {current or '(empty)'} -> {wanted}")
    except OSError as exc:
        log.write(f"microphone: cannot write {path} ({exc})")
    return wanted, default, active


# --------------------------------------------------------------------------- #
# small always-on-top status window
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
        # never take focus: the app must not steal the caret from the window being dictated into
        self.root.update_idletasks()
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x80
        hwnd = self.root.winfo_id()
        if hasattr(user32, "GetWindowLongPtrW"):
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE
                                     | WS_EX_TOOLWINDOW)
        else:
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.root.configure(bg="#111318")
        width, height = 560, 84
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+70")
        self.label = tk.Label(self.root, text="", fg="#f2f4f8", bg="#111318", justify="left",
                              anchor="w", wraplength=width - 24, font=("Segoe UI", 10))
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

    def destroy(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# the hotkey: we only *watch* the real key, so the engine sees a genuine press
# --------------------------------------------------------------------------- #
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
    def __init__(self, vk: int, on_press, on_release) -> None:
        self.vk, self.on_press, self.on_release = vk, on_press, on_release
        self._down = False
        self._proc = HOOKPROC(self._callback)
        self._handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def _callback(self, code, wparam, lparam):  # noqa: ANN001
        # never block here: Windows removes a low-level hook whose callback is slow
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.vkCode == self.vk:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN) and not self._down:
                    self._down = True
                    threading.Thread(target=self.on_press, daemon=True).start()
                elif wparam in (WM_KEYUP, WM_SYSKEYUP) and self._down:
                    self._down = False
                    threading.Thread(target=self.on_release, daemon=True).start()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def close(self) -> None:
        if self._handle:
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None


# --------------------------------------------------------------------------- #
# the application
# --------------------------------------------------------------------------- #
class VoiceApp:
    def __init__(self, runtime: str, cfg: dict, show_overlay: bool = True,
                 log: SessionLog | None = None) -> None:
        exe = os.path.join(runtime, "ImeService.exe")
        if not os.path.exists(exe):
            raise FileNotFoundError(
                f"{exe} is missing.\nKeep the runtime folder next to this program.")
        self.cfg = cfg
        self.log = log or SessionLog(os.path.join(_paths()["logs"], "app.log"), True)
        kill_orphan_engines(runtime, self.log)
        self.show_overlay = show_overlay and bool(cfg.get("show_overlay", True))
        self.overlay = Overlay() if show_overlay else None
        engine_log = os.path.join(_paths()["logs"], f"engine-{os.getpid()}.log")
        self.voice = PlanBVoice(runtime, paste=bool(cfg.get("paste", True)), verbose=False,
                                use_tsf=False, engine_log=engine_log)
        self.engine_log_path = engine_log
        self.engine_log_pos = os.path.getsize(engine_log) if os.path.exists(engine_log) else 0
        self.voice.commit_timeout = float(cfg.get("commit_timeout", 2.5))
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._reset_timer: threading.Timer | None = None
        self.hook: HotkeyHook | None = None
        self.status = "starting"
        self.session_start = 0.0
        self.fg_at_press = 0

    def arm(self) -> None:
        self._status("Starting the engine...")
        set_engine_microphone(str(self.cfg.get("microphone", "")), self.log)
        try:
            self.voice.arm()
        except OSError as exc:
            # the engine died between connecting and the handshake (usually a race with the
            # leftovers we just killed) - start a fresh one and try again
            self.log.write(f"arm failed ({exc}); starting a fresh engine")
            self._restart_engine()
            self.voice.arm()
        stale = self.voice.clear_stale_commit()
        if stale:
            self.log.write(f"cleared a commit left by a previous run: {stale!r}")
        self.vk = VK_NAMES.get(str(self.cfg.get("hotkey", "right alt")).lower(), 0xA5)
        self.log.write(f"engine pid={self.voice.proc.pid} hotkey={self.cfg.get('hotkey')} "
                       f"vk=0x{self.vk:X}")

    def _restart_engine(self) -> None:
        runtime = self.voice.runtime
        try:
            self.voice.close()
        except Exception:
            pass
        kill_orphan_engines(runtime, self.log)
        wait_pipe_gone(PIPE_NAME)
        self.voice = PlanBVoice(runtime, paste=bool(self.cfg.get("paste", True)), verbose=False,
                                use_tsf=False, engine_log=self.engine_log_path)
        self.voice.commit_timeout = float(self.cfg.get("commit_timeout", 2.5))
        self.engine_log_pos = 0

    # ---- engine log (the engine's own decisions, one file per run) --------- #
    def _engine_log_since(self) -> str:
        try:
            with open(self.engine_log_path, "rb") as fh:
                fh.seek(self.engine_log_pos)
                data = fh.read()
                self.engine_log_pos = fh.tell()
            return data.decode("utf-8", "replace")
        except OSError:
            return ""

    def _session_verdict(self, text: str) -> str:
        """Say *why* nothing came back, from what the engine logged during the session."""
        chunk = self._engine_log_since()
        for line in chunk.splitlines():
            if any(k in line for k in ("voice record start", "blocked by unacked",
                                       "not allowed (ime not foreground-active)",
                                       "voice record stop", "DEACTIVATION held_ms")):
                self.log.write("engine: " + line.strip()[:300])
        started = "voice record start ok=1" in chunk
        refused = "voice record start ok=0" in chunk or "blocked by unacked" in chunk
        not_allowed = "not allowed (ime not foreground-active)" in chunk
        words = None
        for line in chunk.splitlines():
            if "online_twopass_result_words_size" in line:
                tail = line.split("online_twopass_result_words_size", 1)[1]
                try:
                    words = float(tail.lstrip(":\", ").split(",")[0])
                except ValueError:
                    words = None
        self.log.write(f"session verdict: started={started} refused={refused} "
                       f"not_allowed={not_allowed} asr_words={words}")
        if text:
            return ""
        if refused:
            return "Engine refused to record (busy) - try again"
        if not_allowed:
            return "Engine needs the focus window - click into it and retry"
        if not started:
            return "Engine did not start recording - see the log"
        if words is not None and words <= 0:
            return "No speech detected - check the microphone"
        return "Nothing recognised"

    def run(self) -> None:
        """Install the hotkey on a thread that pumps messages, then pump this thread."""
        threading.Thread(target=self._hook_loop, daemon=True).start()
        if self.overlay:
            self.overlay.run()          # tkinter keeps the main thread's queue alive
        else:
            while not self._closing.is_set():
                time.sleep(0.5)

    def _hook_loop(self) -> None:
        try:
            self.hook = HotkeyHook(self.vk, self._on_press, self._on_release)
        except Exception as exc:  # noqa: BLE001
            print(f"[{APP_NAME}] cannot watch the hotkey: {exc}", flush=True)
            return
        self._status("Ready - hold the hotkey and speak")
        print(f"[{APP_NAME}] ready ({self.cfg.get('hotkey', 'right alt')} to talk)", flush=True)
        msg = wintypes.MSG()
        # a low-level hook is delivered to the thread that installed it, so this thread must pump
        while not self._closing.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

    def _status(self, text: str) -> None:
        self.status = text
        if self.overlay:
            self.overlay.show(text)
        self.log.write(text)

    def _on_press(self) -> None:
        if self._closing.is_set():
            return
        # blocking on purpose: a quick tap can reach us while the press is still arming
        with self._lock:
            if self._closing.is_set():
                return
            try:
                fg = user32.GetForegroundWindow()
                self.fg_at_press = fg
                self.session_start = time.time()
                self.log.write(f"press: foreground=0x{fg:X} pid={window_pid(fg)} "
                               f"title={window_title(fg)!r}")
                self.voice.on_press()
                if self.overlay:
                    self.overlay.show("Listening...  (release to insert)")
            except Exception as exc:  # noqa: BLE001
                self.log.write(f"error on press: {exc!r}")

    def _on_release(self) -> None:
        if self._closing.is_set():
            return
        with self._lock:
            try:
                self.voice.on_release()
            except Exception as exc:  # noqa: BLE001
                self.log.write(f"error on release: {exc!r}")
                self._status("Ready - hold the hotkey and speak")
                return
        text = self.voice.text
        held = time.time() - self.session_start if self.session_start else 0
        self.log.write(f"release: held={held:.1f}s text={text!r}")
        self._status(f"Inserted: {text}" if text else self._session_verdict(text))
        if self.overlay:
            if self._reset_timer:
                self._reset_timer.cancel()
            self._reset_timer = threading.Timer(
                2.5, lambda: self._status("Ready - hold the hotkey and speak"))
            self._reset_timer.daemon = True
            self._reset_timer.start()

    def close(self) -> None:
        self._closing.set()
        if self._reset_timer:
            self._reset_timer.cancel()
        try:
            if self.hook:
                self.hook.close()
        except Exception:
            pass
        if self.overlay:
            self.overlay.hide()
        try:
            self.voice.close()
        except Exception:
            pass
        user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong,
                                       ctypes.c_void_p]
        for key, flags in RELEASE:
            user32.keybd_event(key, 0, flags, None)
        self.log.write("stopped")
        self.log.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{APP_NAME} (portable, no admin)")
    ap.add_argument("--runtime", default=os.path.join(app_dir(), "runtime"))
    ap.add_argument("--hotkey", help="override the configured hotkey, e.g. 'right alt'")
    ap.add_argument("--microphone", help="endpoint id to record from (see --list-mics)")
    ap.add_argument("--list-mics", action="store_true", help="print the recording devices and exit")
    ap.add_argument("--no-ui", action="store_true")
    args = ap.parse_args()

    if args.list_mics:
        return audio_endpoints.main()

    mutex = single_instance_guard()
    if mutex is None:
        print(f"[{APP_NAME}] another copy is already running", flush=True)
        if not args.no_ui:
            ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} is already running.",
                                             APP_NAME, 0x40)
        return 3

    paths = _paths()
    cfg = load_config(paths["config"])
    if args.hotkey:
        cfg["hotkey"] = args.hotkey
    if args.microphone is not None:
        cfg["microphone"] = args.microphone
    overlay = not args.no_ui
    log = SessionLog(os.path.join(paths["logs"], f"app-{time.strftime('%Y%m%d')}.log"),
                     echo=True)
    log.write(f"runtime = {os.path.abspath(args.runtime)}  windows = "
              f"{sys.getwindowsversion().major}.{sys.getwindowsversion().build}  "
              f"exe = {sys.executable}")
    try:
        app = VoiceApp(os.path.abspath(args.runtime), cfg, show_overlay=overlay, log=log)
    except Exception as exc:  # noqa: BLE001
        log.write(f"cannot start: {exc}")
        log.close()
        if not overlay:
            return 2
        ctypes.windll.user32.MessageBoxW(None, str(exc), APP_NAME, 0x10)
        return 2

    try:
        app.arm()
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
