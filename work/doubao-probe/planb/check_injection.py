"""Plan B: sanity check that our synthetic keys really enter the input queue.

Installs our own WH_KEYBOARD_LL hook, injects a few keys (keybd_event and SendInput variants)
and prints what our hook sees. If we see them but the engine does not, the engine's own hook is
gone (Windows drops low-level hooks whose callback exceeds the timeout).

    python check_injection.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
seen: list[tuple[int, int]] = []


@HOOKPROC
def proc(ncode, wparam, lparam):  # noqa: ANN001
    if ncode == 0:
        info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        seen.append((info.vkCode, int(wparam)))
    return user32.CallNextHookEx(None, ncode, wparam, lparam)


def main() -> int:
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.SetWindowsHookExW.restype = wt.HHOOK
    user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.PeekMessageW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT, wt.UINT]

    # for low-level hooks the module handle may be NULL (the callback lives in this process)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
    print(f"[info] our hook = {hook} err={ctypes.get_last_error()}")
    if not hook:
        return 1

    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
    print("[inject] keybd_event A down/up")
    user32.keybd_event(0x41, 0, 0, None)
    time.sleep(0.05)
    user32.keybd_event(0x41, 0, 2, None)
    for _ in range(40):
        user32.PeekMessageW(None, None, 0, 0, 1)
        time.sleep(0.01)

    print(f"[seen] {[(hex(v), hex(w)) for v, w in seen]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
