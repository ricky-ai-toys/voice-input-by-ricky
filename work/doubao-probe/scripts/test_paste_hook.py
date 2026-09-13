"""Verify the injection keystroke without needing to steal focus.

Installs a low-level keyboard hook, runs paste_text(), and checks that a real
Ctrl+V sequence reached the OS while the clipboard held the text.
"""
import os
import sys
import threading
import time

import keyboard

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import doubao_voice  # noqa: E402

TEXT = "粘贴测试：你好，豆包语音输入。"


def main() -> int:
    events: list[tuple[str, str]] = []
    hook = keyboard.hook(lambda e: events.append((e.name or "", e.event_type)))
    time.sleep(0.4)

    doubao_voice.paste_text(TEXT, {"restore_clipboard": False, "paste_delay_ms": 60})
    time.sleep(0.5)

    clip = doubao_voice.get_clipboard_text() or ""
    ctrl_down = any("ctrl" in name and kind == "down" for name, kind in events)
    v_down = any(name == "v" and kind == "down" for name, kind in events)
    v_up = any(name == "v" and kind == "up" for name, kind in events)
    keyboard.unhook(hook)

    print(f"clipboard content : {clip!r}")
    print(f"ctrl down seen    : {ctrl_down}")
    print(f"'v' down/up seen  : {v_down}/{v_up}")
    print(f"observed key events: {[e for e in events if e[1] == 'down'][:15]}")
    ok = clip == TEXT and ctrl_down and v_down and v_up
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
