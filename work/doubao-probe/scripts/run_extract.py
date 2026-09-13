"""Spawn ImeService with --test-sami under frida and print the recognized text.

Uses extract_text.js, which returns the raw result JSON base64-encoded so the
recognized characters survive any console encoding issues.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time

import frida


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--script", default="", help="frida script (default: extract_text.js next to this file)")
    ap.add_argument("--out", default="", help="write the final text to this file (utf-8)")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    script_path = args.script or os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract_text.js")
    with open(script_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    argv = ["--test-sami", "--wav", args.wav]
    cwd = os.path.dirname(args.exe)

    pid = frida.spawn(args.exe, argv=[args.exe] + argv, cwd=cwd, stdio="inherit")
    session = frida.attach(pid)
    script = session.create_script(source)

    state = {"last_text": "", "final_text": "", "results": 0}

    def on_message(message, data):
        payload = message.get("payload") or {}
        if message.get("type") != "send":
            return
        kind = payload.get("kind")
        if kind == "json":
            raw = payload.get("b64") or ""
            try:
                doc = json.loads(base64.b64decode(raw).decode("utf-8"))
            except Exception:
                try:
                    doc = json.loads(payload.get("text") or "")
                except Exception:
                    return
            for item in doc.get("results") or []:
                text = item.get("text") or ""
                if not text:
                    continue
                state["results"] += 1
                state["last_text"] = text
                if item.get("is_vad_finished") or item.get("is_interim") is False:
                    state["final_text"] = text
        elif kind == "log":
            line = payload.get("text", "")
            if "sami test result valid=" in line:
                print(f"[svc] {line}", flush=True)
        elif kind in ("error", "ready"):
            print(f"[frida:{kind}] {payload}", flush=True)

    script.on("message", on_message)
    script.load()
    frida.resume(pid)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True
        ).stdout
        if str(pid) not in out:
            break
        time.sleep(1)
    else:
        try:
            frida.kill(pid)
        except Exception:
            pass

    text = state["final_text"] or state["last_text"]
    print(f"[result] events={state['results']} text={text!r}", flush=True)
    if args.out and text:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
