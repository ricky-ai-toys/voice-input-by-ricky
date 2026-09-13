"""Spawn ImeService.exe with the --test-sami file test under frida.

Optionally patches the AppLog DID guard so the file test can proceed before the
applog runtime publishes the cached device id.
"""
import argparse
import ctypes
import os
import subprocess
import sys
import time

import frida

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def exit_code_of(pid: int):
    """Best-effort exit code for a pid we spawned."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        k32.WaitForSingleObject(handle, 20000)
        code = ctypes.c_ulong(0)
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value
        return None
    finally:
        k32.CloseHandle(handle)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--script", default="")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    argv = ["--test-sami", "--wav", args.wav]
    cwd = os.path.dirname(args.exe)

    env = dict(os.environ)
    env["OIME_SERVER_CONSOLE"] = "1"

    print(f"[driver] spawning {args.exe} {argv}", flush=True)
    # NOTE: frida on Windows drops argv unless argv[0] is the executable itself.
    pid = frida.spawn(args.exe, argv=[args.exe] + argv, cwd=cwd, env=env, stdio="inherit")
    print(f"[driver] pid={pid}", flush=True)

    session = frida.attach(pid)
    if args.script:
        with open(args.script, "r", encoding="utf-8") as fh:
            source = fh.read()
        script = session.create_script(source)

        def on_message(message, data):
            print(f"[frida] {message}", flush=True)

        script.on("message", on_message)
        script.load()
        print("[driver] script loaded", flush=True)

    frida.resume(pid)
    print("[driver] resumed", flush=True)

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        ).stdout
        if str(pid) not in out:
            print(f"[driver] process {pid} exited", flush=True)
            code = exit_code_of(pid)
            if code is not None:
                print(f"[driver] exit code = {code} (0x{code & 0xFFFFFFFF:08X})", flush=True)
            break
        time.sleep(2)
    else:
        print(f"[driver] timeout after {args.timeout}s, killing {pid}", flush=True)
        try:
            session.detach()
            frida.kill(pid)
        except Exception as exc:  # noqa: BLE001
            print(f"[driver] kill failed: {exc}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
