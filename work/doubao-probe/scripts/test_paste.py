"""End-to-end check of the clipboard + Ctrl+V injection path.

Opens a Tk text box, focuses it, injects text through doubao_voice.paste_text,
then reads the widget contents back.
"""
import os
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import doubao_voice  # noqa: E402

TEXT = "粘贴测试：你好，豆包语音输入。"


def main() -> int:
    root = tk.Tk()
    root.title("paste target")
    root.geometry("520x160+200+200")
    box = tk.Text(root)
    box.pack(expand=True, fill="both")
    root.attributes("-topmost", True)
    root.after(300, lambda: (root.focus_force(), box.focus_force()))

    result = {"content": "", "clip": ""}

    def worker() -> None:
        time.sleep(1.5)
        root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force(), box.focus_force()))
        time.sleep(0.4)
        import ctypes

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        target = root.winfo_id()
        fg = u32.GetForegroundWindow()
        fg_thread = u32.GetWindowThreadProcessId(fg, None)
        cur_thread = k32.GetCurrentThreadId()
        u32.AttachThreadInput(fg_thread, cur_thread, True)
        try:
            u32.SetForegroundWindow(target)
            u32.SetFocus(target)
            time.sleep(0.3)
            buf = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(u32.GetForegroundWindow(), buf, 256)
            print(f"[diag] foreground now: {buf.value!r}", flush=True)
        finally:
            u32.AttachThreadInput(fg_thread, cur_thread, False)
        doubao_voice.paste_text(TEXT, {"restore_clipboard": False, "paste_delay_ms": 60})
        time.sleep(1.5)
        result["content"] = box.get("1.0", "end").strip()
        result["clip"] = doubao_voice.get_clipboard_text() or ""
        root.after(0, root.destroy)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()

    ok = result["content"] == TEXT
    print(f"expected : {TEXT!r}")
    print(f"typed    : {result['content']!r}")
    print(f"clipboard: {result['clip']!r}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
