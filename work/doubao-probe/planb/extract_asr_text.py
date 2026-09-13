"""Pull the recognized text out of an engine log.

The ASR results arrive as protobuf-JSON in the engine's own log:

    ... payload: {"results":[{"is_interim":true,"text":"..."}], ...}

    python extract_asr_text.py scratch/pipe_client_server.log [--unique]
"""
from __future__ import annotations

import re
import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "scratch/pipe_client_server.log"
    unique = "--unique" in sys.argv
    data = open(path, encoding="utf-8", errors="replace").read()
    items = re.findall(r'"is_interim":(\w+),"text":"([^"]*)"', data)
    print(f"result items: {len(items)}, non-empty: {sum(1 for _, t in items if t)}")
    seen = []
    for interim, text in items:
        if not text:
            continue
        if unique and (interim, text) in seen:
            continue
        seen.append((interim, text))
    for interim, text in seen:
        print(f"  interim={interim} text={text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
