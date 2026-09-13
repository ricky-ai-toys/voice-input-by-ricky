"""Plan B helper: print the method order of a TSF interface from Wine's msctf.idl.

Used to confirm vtable slot indices (Activate=3, CreateDocumentMgr=5, CreateContext=3, ...).

    python dump_tsf_iface.py ITfDocumentMgr ITfThreadMgr ITfKeyEventSink
"""
import re
import sys
import urllib.request

URL = "https://raw.githubusercontent.com/wine-mirror/wine/master/include/msctf.idl"


def main() -> int:
    text = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8", "ignore")
    for name in sys.argv[1:] or ["ITfDocumentMgr"]:
        m = re.search(rf"interface\s+{name}\s*:", text)
        if not m:
            print(f"### {name}: not found")
            continue
        tail = text[m.start():]
        stop = len(tail)
        for mm in re.finditer(r"^\}\s*;?\s*$", tail, flags=re.MULTILINE):
            stop = mm.start()
            break
        block = tail[:stop]
        methods = [ln.strip() for ln in block.splitlines()
                   if re.match(r"\s*(HRESULT|void|ULONG|BOOL)\s+\w+\s*\(", ln)]
        print(f"### {name}  (slots after IUnknown, i.e. starting at 3)")
        for i, sig in enumerate(methods):
            print(f"  {i + 3:2d}: {sig}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
