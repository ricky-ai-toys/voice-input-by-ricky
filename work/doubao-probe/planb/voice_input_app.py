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

from planb_voice_input import PlanBVoice
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


def app_dir() -> str:
    """Folder that holds the exe (frozen) or this script (source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_config() -> dict:
    return {"hotkey": "right alt", "show_overlay": True, "paste": True, "commit_timeout": 2.5}


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
    def __init__(self, runtime: str, cfg: dict, show_overlay: bool = True) -> None:
        exe = os.path.join(runtime, "ImeService.exe")
        if not os.path.exists(exe):
            raise FileNotFoundError(
                f"{exe} is missing.\nKeep the runtime folder next to this program.")
        self.cfg = cfg
        self.show_overlay = show_overlay and bool(cfg.get("show_overlay", True))
        self.overlay = Overlay() if show_overlay else None
        self.voice = PlanBVoice(runtime, paste=bool(cfg.get("paste", True)), verbose=False,
                                use_tsf=False)
        self.voice.commit_timeout = float(cfg.get("commit_timeout", 2.5))
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._reset_timer: threading.Timer | None = None
        self.hook: HotkeyHook | None = None
        self.status = "starting"

    def arm(self) -> None:
        self._status("Starting the engine...")
        self.voice.arm()
        self.vk = VK_NAMES.get(str(self.cfg.get("hotkey", "right alt")).lower(), 0xA5)

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
        print(f"[{APP_NAME}] {text}", flush=True)

    def _on_press(self) -> None:
        if self._closing.is_set():
            return
        # blocking on purpose: a quick tap can reach us while the press is still arming
        with self._lock:
            if self._closing.is_set():
                return
            try:
                self.voice.on_press()
                if self.overlay:
                    self.overlay.show("Listening...  (release to insert)")
            except Exception as exc:  # noqa: BLE001
                print(f"[{APP_NAME}] error: {exc}", flush=True)

    def _on_release(self) -> None:
        if self._closing.is_set():
            return
        with self._lock:
            try:
                self.voice.on_release()
            except Exception as exc:  # noqa: BLE001
                print(f"[{APP_NAME}] error: {exc}", flush=True)
                self._status("Ready - hold the hotkey and speak")
                return
        text = self.voice.text
        self._status(f"Inserted: {text}" if text else "Nothing recognised")
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


def main() -> int:
    ap = argparse.ArgumentParser(description=f"{APP_NAME} (portable, no admin)")
    ap.add_argument("--runtime", default=os.path.join(app_dir(), "runtime"))
    ap.add_argument("--hotkey", help="override the configured hotkey, e.g. 'right alt'")
    ap.add_argument("--no-ui", action="store_true")
    args = ap.parse_args()

    cfg = load_config(os.path.join(app_dir(), "data", "config.json"))
    if args.hotkey:
        cfg["hotkey"] = args.hotkey
    overlay = not args.no_ui
    print(f"[{APP_NAME}] runtime = {os.path.abspath(args.runtime)}", flush=True)
    try:
        app = VoiceApp(os.path.abspath(args.runtime), cfg, show_overlay=overlay)
    except Exception as exc:  # noqa: BLE001
        print(f"[{APP_NAME}] cannot start: {exc}", flush=True)
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
