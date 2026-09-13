"""Plan B: decode the core's log stream captured from the -tsf-log pipe.

`run_with_hooks.py` prints pipe writes as hex plus a rough ASCII rendering; this tool turns
that output back into clean log lines (the TLOG framing ends before the first '[' of the
timestamp, so the text can be recovered even though it is wrapped in dots).

    python run_with_hooks.py scratch host_tip_harness.py > capture.txt
    python decode_tlog.py capture.txt [--grep voice]
"""
import re
import sys


def main() -> int:
    path = sys.argv[1]
    pattern = None
    if len(sys.argv) > 3 and sys.argv[2] == "--grep":
        pattern = re.compile(sys.argv[3], re.I)

    lines: list[str] = []
    for raw in open(path, encoding="utf-8", errors="ignore"):
        if "text=" not in raw or "TLOG" not in raw:
            continue
        body = raw.split("text=", 1)[1]
        # keep only the human readable parts: timestamps and bracketed messages
        for chunk in re.findall(r"\[[^\[\]]{4,200}\]", body):
            lines.append(chunk)
    out = [ln for ln in lines if not pattern or pattern.search(ln)]
    seen = []
    for ln in out:
        if ln not in seen:
            seen.append(ln)
    print("\n".join(seen[:200]))
    print(f"[decode] {len(lines)} log chunks, {len(seen)} unique"
          + (f", filtered by /{pattern.pattern}/" if pattern else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
