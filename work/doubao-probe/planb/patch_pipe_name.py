"""Plan B prep: move the vendor engine onto a private pipe name.

The engine's pipe is `\\\\.\\pipe\\ObricIme\\oime-server`. On a machine that also has the
official Doubao IME installed, a resident copy of the engine would collide with the running
instance, so a fork should rename the pipe inside its own copy of `ImeService.exe` and
`rpc.dll` (same byte length, so no PE surgery is needed).

Usage:
    python patch_pipe_name.py <runtime_dir> [--suffix R]
"""
import argparse
import os
import sys

OLD = b"\\\\.\\pipe\\ObricIme\\oime-server"


def patched_name(suffix: str) -> bytes:
    if len(suffix) != 1:
        raise SystemExit("suffix must be exactly one character (keeps the length identical)")
    return OLD[:-1] + suffix.encode("ascii")


def patch_file(path: str, new: bytes) -> int:
    with open(path, "rb") as fh:
        data = fh.read()
    count = data.count(OLD)
    if count == 0:
        return 0
    backup = path + ".orig"
    if not os.path.exists(backup):
        with open(backup, "wb") as fh:
            fh.write(data)
    with open(path, "wb") as fh:
        fh.write(data.replace(OLD, new))
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runtime_dir")
    ap.add_argument("--suffix", default="R")
    args = ap.parse_args()

    new = patched_name(args.suffix)
    targets = [os.path.join(args.runtime_dir, name) for name in ("ImeService.exe", "rpc.dll")]
    total = 0
    for path in targets:
        if not os.path.exists(path):
            print(f"[skip] {path} not found")
            continue
        n = patch_file(path, new)
        total += n
        print(f"[ok] {os.path.basename(path)}: {n} occurrence(s) -> {new.decode()}")
    print(f"total patched: {total}")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
