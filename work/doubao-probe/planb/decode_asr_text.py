"""Recover the recognized text from the engine log (its log writer mangles the encoding).

    python decode_asr_text.py scratch/pipe_client_server.log
"""
from __future__ import annotations

import re
import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "scratch/pipe_client_server.log"
    blob = open(path, "rb").read()
    hits = re.findall(rb'"text":"([^"]*)"', blob)
    print(f"{len(hits)} text field(s)")
    seen = []
    for raw in hits:
        if not raw:
            continue
        variants = {}
        for enc in ("utf-8", "gbk", "cp1252", "latin-1", "utf-16-le"):
            try:
                variants[enc] = raw.decode(enc)
            except Exception:
                continue
        if raw not in seen:
            seen.append(raw)
            print(f"  raw={raw.hex()}")
            for enc, text in variants.items():
                if text.strip():
                    print(f"      {enc:9s} -> {text!r}")
            # the engine writes UTF-8 bytes into a stream that is then read back as latin-1
            try:
                fixed = raw.decode("latin-1").encode("latin-1").decode("utf-8")
                print(f"      latin1->utf8 -> {fixed!r}")
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
