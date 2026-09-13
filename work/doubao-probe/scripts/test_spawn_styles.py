"""Try several frida.spawn argument styles and report what the child receives."""
import os
import sys
import time

import frida


def run(style: str, exe: str, wav: str, script_src: str) -> None:
    base = ["--test-sami", "--wav", wav]
    print(f"\n=== style={style} ===", flush=True)
    if style == "args-only":
        pid = frida.spawn(exe, argv=base, cwd=os.path.dirname(exe), stdio="inherit")
    elif style == "with-exe":
        pid = frida.spawn(exe, argv=[exe] + base, cwd=os.path.dirname(exe), stdio="inherit")
    elif style == "list":
        pid = frida.spawn([exe] + base, cwd=os.path.dirname(exe), stdio="inherit")
    else:
        raise SystemExit(f"unknown style {style}")

    session = frida.attach(pid)
    script = session.create_script(script_src)
    seen = []

    def on_message(message, data):
        payload = message.get("payload") or {}
        if message.get("type") == "send":
            kind = payload.get("kind")
            if kind == "cmdline":
                print(f"  cmdline: {payload['value']}", flush=True)
            elif kind == "stdout":
                text = payload.get("text", "").strip()
                if text:
                    seen.append(text)
                    print(f"  out: {text}", flush=True)
            elif kind in ("guard-hit", "patched", "error"):
                print(f"  [{kind}] {payload}", flush=True)
        else:
            print(f"  [message] {message}", flush=True)

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    for _ in range(30):
        time.sleep(1)
        if str(pid) not in os.popen(f'tasklist /FI "PID eq {pid}" /NH').read():
            break
    else:
        frida.kill(pid)


def main() -> int:
    exe = sys.argv[1]
    wav = sys.argv[2]
    script_path = sys.argv[3]
    styles = sys.argv[4:] or ["args-only", "with-exe", "list"]
    src = open(script_path, encoding="utf-8").read()
    for style in styles:
        run(style, exe, wav, src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
