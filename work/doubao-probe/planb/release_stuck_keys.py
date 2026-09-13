"""Release any key the OS still thinks is held down.

The voice-hook experiments inject a Right Alt press. If the engine's hook swallows the matching
release (that is exactly what our patched copy does - "real UP eaten + synthesized release"),
Windows keeps Alt logically down and every keypress turns into an Alt shortcut.

    python release_stuck_keys.py [--check-only]
"""
from __future__ import annotations

import ctypes
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ulong,
                               ctypes.c_void_p]

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

# left/right variants first, then the generic VK, so the OS state machine is fully cleared
RELEASE = [(0xA4, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY),   # LAlt
           (0xA5, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY),   # RAlt
           (0xA2, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY),   # LCtrl
           (0xA3, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY),   # RCtrl
           (0xA0, KEYEVENTF_KEYUP),                           # LShift
           (0xA1, KEYEVENTF_KEYUP),                           # RShift
           (0x5B, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY),   # LWin
           (0x5C, KEYEVENTF_KEYUP | KEYEVENTF_EXTENDEDKEY),   # RWin
           (0x12, KEYEVENTF_KEYUP),                           # Alt
           (0x11, KEYEVENTF_KEYUP),                           # Ctrl
           (0x10, KEYEVENTF_KEYUP)]                           # Shift


def down_keys() -> list[int]:
    return [vk for vk in range(1, 0xFF) if user32.GetAsyncKeyState(vk) & 0x8000]


def main() -> int:
    before = down_keys()
    print(f"[state] keys the OS thinks are down: {[hex(v) for v in before]}")
    if "--check-only" in sys.argv:
        return 0
    if not before:
        print("[ok] nothing to release")
        return 0
    for vk, flags in RELEASE:
        user32.keybd_event(vk, 0, flags, None)
        time.sleep(0.02)
    time.sleep(0.2)
    after = down_keys()
    print(f"[state] after release: {[hex(v) for v in after]}")
    print("[ok] modifiers released" if not after else "[warn] keys still held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
