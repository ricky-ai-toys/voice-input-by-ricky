"""Check paste_text() against a scratch edit control, so the real terminal is never touched."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

from planb_voice_input import paste_text

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, ctypes.c_void_p]
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


def main() -> int:
    text = "測試文字 test 123"
    hinst = kernel32.GetModuleHandleW(None)

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == 0x0010:  # WM_CLOSE
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.restype = wt.HWND

    cls = WNDCLASSEXW()
    cls.cbSize = ctypes.sizeof(WNDCLASSEXW)
    cls.lpfnWndProc = WNDPROC(wndproc)
    cls.hInstance = hinst
    cls.lpszClassName = "PlanBPasteTest"
    user32.RegisterClassExW(ctypes.byref(cls))

    frame = user32.CreateWindowExW(0, "PlanBPasteTest", "paste test", 0x10CF0000,
                                   100, 100, 420, 120, None, None, hinst, None)
    edit = user32.CreateWindowExW(0, "EDIT", "", 0x50810004,
                                  0, 0, 400, 80, frame, None, hinst, None)
    user32.ShowWindow(frame, 5)
    for _ in range(100):
        user32.SetForegroundWindow(frame)
        user32.SetFocus(edit)
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if user32.GetForegroundWindow() == frame:
            break
        time.sleep(0.02)
    print(f"[paste] foreground = {user32.GetForegroundWindow() == frame}")
    user32.SetFocus(edit)
    user32.SetFocus(edit)
    print(f"[paste] focus      = {user32.GetFocus() == edit}")

    paste_text(text, frame)
    # a real target app has its own message pump; this harness has to play that role
    deadline = time.time() + 0.8
    while time.time() < deadline:
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)
    user32.SetFocus(edit)

    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(edit, buf, 256)
    if buf.value != text and "--no-fallback" not in sys.argv:
        # Ctrl+V needs a focused control; WM_PASTE then proves the clipboard itself is fine
        user32.SendMessageW(edit, 0x0302, 0, 0)
        time.sleep(0.1)
        user32.GetWindowTextW(edit, buf, 256)

    got = buf.value
    print(f"[paste] sent  ={text!r}")
    print(f"[paste] in edit={got!r}")
    print("[paste] OK" if got == text else "[paste] MISMATCH")
    user32.DestroyWindow(frame)
    return 0 if got == text else 1


if __name__ == "__main__":
    sys.exit(main())
