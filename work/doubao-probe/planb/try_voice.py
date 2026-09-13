"""Plan B: try to make the engine accept a voice key press from our own client.

The engine logs, for every key it processes:

    [context][controller] apply-before-input key=32 no-valid-context

`no-valid-context` means the controller has no host context for the requester, so the key is
dropped. The candidate that creates that context is op 0x14 (UpdateHostContextUtf8), whose
body is a run of length-prefixed strings (see oracle_client.py output).

    python try_voice.py scratch [--fields pid,proc,class] [--key 165]
"""
from __future__ import annotations

import os
import sys
import time
import ctypes
import ctypes.wintypes as wt

from pipe_client import Pipe, log_grep, pb_int, pb_str, start_server

OP_UPDATE_HOST_CONTEXT = 0x14
OP_KEYDOWN = 0x04
OP_KEYUP = 0x05
OP_FOCUS_IN = 0x06
OP_PEEK = 0x17


def lp(text: str) -> bytes:
    raw = text.encode("utf-8")
    return len(raw).to_bytes(4, "little") + raw


# ctypes callbacks must outlive the window they are registered for, otherwise Windows calls
# into freed memory as soon as the window receives a message
_KEEP_ALIVE: list = []


def make_foreground_window(activate: bool = True) -> int:
    """A real window + EDIT control, brought to the foreground.

    The engine decides whether a client is allowed to drive input by looking at the
    requester's window (its logs mention requester_pid vs foreground_pid), so the test
    client has to own the foreground window the same way a normal app does.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM,
                                 ctypes.c_ssize_t)
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wt.HINSTANCE), ("hIcon", wt.HANDLE),
                    ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE),
                    ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]

    @WNDPROC
    def proc(hwnd, msg, wparam, lparam):  # noqa: ANN001
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
    user32.RegisterClassW.argtypes = [ctypes.c_void_p]
    user32.RegisterClassW.restype = wt.WORD
    user32.CreateWindowExW.restype = wt.HWND
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wt.HWND, wt.HANDLE, wt.HINSTANCE, ctypes.c_void_p]
    cls = WNDCLASSW()
    cls.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    _KEEP_ALIVE.extend([proc, cls])
    cls.hInstance = hinst
    cls.lpszClassName = "RickyVoicePlanBHost"
    user32.RegisterClassW(ctypes.byref(cls))
    hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "Ricky Voice Plan B host",
                                  wt.DWORD(0x00CF0000), 200, 200, 520, 180,
                                  None, None, hinst, None)
    user32.ShowWindow(hwnd, 5)          # SW_SHOW
    if not activate:
        # keep it a plain hidden-ish host: the engine only needs the hwnd to post to
        user32.ShowWindow(hwnd, 0)      # SW_HIDE
        edit = user32.CreateWindowExW(0, "EDIT", "", wt.DWORD(0x50010000), 10, 10, 480, 120,
                                      hwnd, None, hinst, None)
        user32.SetFocus(edit)
        return hwnd
    # Windows only lets the *foreground* process steal focus, so borrow the current
    # foreground thread's input queue for a moment (the classic AttachThreadInput trick).
    user32.GetForegroundWindow.restype = wt.HWND
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wt.DWORD
    user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    user32.BringWindowToTop.argtypes = [wt.HWND]
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != my_tid:
        attached = bool(user32.AttachThreadInput(fg_tid, my_tid, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    if attached:
        user32.AttachThreadInput(fg_tid, my_tid, False)
    edit = user32.CreateWindowExW(0, "EDIT", "", wt.DWORD(0x50010000), 10, 10, 480, 120,
                                 hwnd, None, hinst, None)
    user32.SetFocus(edit)
    return hwnd


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    fields = ["12345", "python.exe", "DoubaoHarnessHostWnd"]
    key = 165
    args = sys.argv[2:]
    if "--fields" in args:
        fields = args[args.index("--fields") + 1].split(",")
    if "--key" in args:
        key = int(args[args.index("--key") + 1], 0)
    arm_tryout = "--arm-tryout" in sys.argv

    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    if arm_tryout:
        # the voice hotkey stays disabled until the settings channel reports "voice tryout
        # active"; a plain client can say so itself (captured protocol, see
        # settings_ipc_client.py)
        try:
            from settings_ipc_client import SettingsPipe, request
            # the settings server consumes one pipe instance per connection, so reconnect
            sp = SettingsPipe()
            response = sp.call(request("settings.setVoiceTryoutActive",
                                       {"active": True, "cookie": int(time.time())}))
            print(f"[arm] setVoiceTryoutActive(True) -> {response}")
            sp.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] could not arm voice tryout: {exc}")
    hwnd = make_foreground_window()
    print(f"[info] host window=0x{hwnd:X} (foreground)")
    try:
        pipe = Pipe(timeout=35.0)
    except OSError as exc:
        print(f"[fail] {exc}")
        log_grep(runtime, 10)
        return 1

    # exact op sequence the vendor's own TSF core sends when it takes focus in a text host
    # (captured with run_with_hooks.py + host_tip_harness.py)
    sequence = [
        (0x1B, bytes.fromhex("0801"), "editable/state"),
        (0x06, (0x48F8).to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
         "FocusIn(host)"),
        (0x0C, bytes.fromhex("08f8071801"), "SetUIElementShowState"),
        (0x15, bytes.fromhex("ba0c2b000000000001000000914341d5"), "op15"),
        # UpdateHostContext: [str][str][str][u64][str][str] - layout from oracle_client.py
        (0x14, lp("") + lp("") + lp("") + int(hwnd).to_bytes(8, "little")
         + lp("planb") + lp("python.exe"), "UpdateHostContext"),
    ]
    for op, body, name in sequence:
        res = pipe.call(op, body)
        print(f"[{name}] op=0x{op:02X} -> {res if res is None else res[2]}")

    host = os.path.basename(sys.executable or "python.exe")
    # The engine installs its own global keyboard hook ("voice key hook installed"), so the
    # voice hotkey never travels over the pipe: the key must really be pressed while our
    # window owns the foreground, and the text comes back through PeekVoiceCommit.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
    user32.keybd_event(0xA5, 0, 0, None)
    print("[key] Right Alt held (physical)")
    for i in range(8):
        time.sleep(0.4)
        res = pipe.call(OP_PEEK, b"")
        print(f"   [peek {i}] {res if res is None else (res[2], res[1].hex())}")
    user32.keybd_event(0xA5, 0, 2, None)   # KEYEVENTF_KEYUP
    print("[key] Right Alt released")
    for i in range(4):
        time.sleep(0.5)
        res = pipe.call(OP_PEEK, b"")
        print(f"   [peek after {i}] {res if res is None else (res[2], res[1].hex())}")
    res = pipe.call(OP_KEYDOWN, pb_int(1, key) + pb_str(3, host))
    print(f"[keydown rpc {key}] -> {res if res is None else res[2]}")
    time.sleep(0.5)
    res = pipe.call(OP_KEYUP, pb_int(1, key) + pb_str(3, host))
    print(f"[keyup rpc {key}] -> {res if res is None else res[2]}")

    pipe.close()
    log_grep(runtime, 20)
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
