"""Plan B: extract the exact ITextStoreACP method order from Wine's msctf.idl.

The host harness must implement ITextStoreACP; the vtable order has to match the SDK exactly.
Wine's IDL is a faithful mirror and is available without the Windows SDK.

    python extract_textstore_acp.py
"""
import os
import re
import sys
import urllib.request

URLS = [
    "https://raw.githubusercontent.com/wine-mirror/wine/master/include/textstor.idl",
    "https://raw.githubusercontent.com/wine-mirror/wine/master/include/msctf.idl",
]
IFACE = "ITextStoreACP"


def main() -> int:
    text = ""
    for url in URLS:
        try:
            text += urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore") + "\n"
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {url}: {exc}")
    if not text:
        print("[fail] no IDL source available")
        return 1

    m = re.search(rf"interface\s+{IFACE}\s*:", text)
    if not m:
        print(f"[fail] {IFACE} not found in the IDL")
        return 1
    start = m.start()
    # the interface body ends at the first line that is just "}" (Wine writes "}" then ";")
    tail = text[start:]
    stop = len(tail)
    for m in re.finditer(r"^\}\s*;?\s*$", tail, flags=re.MULTILINE):
        stop = m.start()
        break
    block = tail[:stop]

    methods = []
    for line in block.splitlines():
        line = line.strip()
        m = re.match(r"(?:HRESULT|void|ULONG|BOOL)\s+\w+\s*\(", line)
        if m and not line.startswith("interface"):
            methods.append(line)

    out_lines = [f"{i:2d}. {m}" for i, m in enumerate(methods)]
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "itextstoreacp_methods.txt")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")
    print("\n".join(out_lines[:24]))
    print(f"[ok] {len(methods)} methods -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
