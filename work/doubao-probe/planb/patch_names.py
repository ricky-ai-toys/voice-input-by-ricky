"""Plan B prep: give the vendor engine a private identity inside our copy.

Two names keep a second engine instance from coexisting with the official Doubao IME:

* the RPC pipe        `\\\\.\\pipe\\ObricIme\\oime-server`  (ASCII literal)
* the single instance `ObricImeServerSingleInstance`         (UTF-16LE literal)

Both are rewritten to a private name of **identical length** (one character swapped),
which keeps the PE layout untouched and needs no code caves.

Usage:
    python patch_names.py <dir-with-runtime> [--suffix R]
"""
import argparse
import os
import sys

ASCII_PAIRS = [
    (b"\\\\.\\pipe\\ObricIme\\oime-server", b"\\\\.\\pipe\\ObricIme\\oime-serve"),
]
UTF16_PAIRS = [
    ("ObricImeServerSingleInstance", "ObricImeServerSingleInstanc"),
]
TARGETS = ("ImeService.exe", "rpc.dll")


def patch(path: str, suffix: str) -> int:
    with open(path, "rb") as fh:
        data = fh.read()
    original = data
    total = 0
    for old, new in ASCII_PAIRS:
        old_b, new_b = old, new + suffix.encode("ascii")
        if len(old_b) != len(new_b):
            raise SystemExit("ascii pair length mismatch")
        n = data.count(old_b)
        if n:
            data = data.replace(old_b, new_b)
            total += n
    for old_s, new_s in UTF16_PAIRS:
        old_b = old_s.encode("utf-16-le")
        new_b = (new_s + suffix).encode("utf-16-le")
        if len(old_b) != len(new_b):
            raise SystemExit("utf16 pair length mismatch")
        n = data.count(old_b)
        if n:
            data = data.replace(old_b, new_b)
            total += n
    if data != original:
        if len(data) != len(original):
            raise SystemExit("size changed, aborting")
        backup = path + ".orig"
        if not os.path.exists(backup):
            with open(backup, "wb") as fh:
                fh.write(original)
        with open(path, "wb") as fh:
            fh.write(data)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runtime_dir")
    ap.add_argument("--suffix", default="R")
    args = ap.parse_args()

    total = 0
    for name in TARGETS:
        path = os.path.join(args.runtime_dir, name)
        if not os.path.exists(path):
            print(f"[skip] {name} not found")
            continue
        found = 0
        with open(path, "rb") as fh:
            text = fh.read()
        for old, _new in ASCII_PAIRS:
            found += text.count(old)
        for old_s, _new in UTF16_PAIRS:
            found += text.count(old_s.encode("utf-16-le"))
        n = patch(path, args.suffix)
        total += n
        print(f"[ok] {name}: patched {n}/{found} occurrence(s)")
    print(f"total patched: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
