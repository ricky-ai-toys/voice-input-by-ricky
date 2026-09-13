"""Plan B: collect the TSF interface IIDs we need (no Windows SDK on this machine).

Source: Wine's msctf.idl (mirror of the Windows SDK interface definitions).
Saves the ids used by the host harness into tsf_iids.txt.

    python fetch_tsf_iids.py
"""
import os
import re
import sys
import urllib.request

URL = "https://raw.githubusercontent.com/wine-mirror/wine/master/include/msctf.idl"
WANT = [
    "ITfThreadMgr", "ITfThreadMgr2", "ITfThreadMgrEventSink", "ITfDocumentMgr",
    "ITfContext", "ITfTextInputProcessor", "ITfTextInputProcessorEx",
    "ITfKeyEventSink", "ITextStoreACP", "ITfContextOwnerCompositionSink",
    "ITfInputProcessorProfiles", "ITfUIElementSink",
]


def main() -> int:
    try:
        text = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] {exc}")
        return 1

    # Wine's IDL puts several attributes between uuid(...) and the interface keyword
    pairs = []
    for m in re.finditer(r"uuid\(([0-9a-fA-F\-]{36})\)", text):
        tail = text[m.end():m.end() + 600]
        im = re.search(r"interface\s+(\w+)", tail)
        if im:
            pairs.append((m.group(1), im.group(1)))
    found = {name: uuid for uuid, name in pairs if name in WANT}
    lines = [f"{name:32s} {{{uuid.upper()}}}" for name, uuid in sorted(found.items())]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tsf_iids.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"[ok] wrote {out} ({len(lines)} of {len(WANT)} interfaces found, "
          f"{len(pairs)} interfaces parsed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
