"""Plan B helper: find the CLSIDs/IIDs used to activate an input profile in TSF.

Needed for the host harness: activating the Doubao profile makes the text service consider
itself the active IME (which is when it installs its key handling / push-to-talk).

    python fetch_tsf_coclasses.py
"""
import re
import sys
import urllib.request

URL = "https://raw.githubusercontent.com/wine-mirror/wine/master/include/msctf.idl"
WANT = ["ITfInputProcessorProfileMgr", "InputProcessorProfileMgr",
        "TF_InputProcessorProfiles", "InputProcessorProfiles"]


def main() -> int:
    text = urllib.request.urlopen(URL, timeout=30).read().decode("utf-8", "ignore")
    for m in re.finditer(r"uuid\(([0-9a-fA-F\-]{36})\)[^}]*?interface\s+(\w+)|"
                         r"uuid\(([0-9a-fA-F\-]{36})\)[^}]*?coclass\s+(\w+)", text):
        iface = m.group(2) or ""
        coclass = m.group(4) or ""
        name = iface or coclass
        if any(w.lower() in name.lower() for w in WANT):
            uuid = m.group(1) or m.group(3)
            kind = "interface" if iface else "coclass"
            print(f"{kind:9s} {name:32s} {{{uuid.upper()}}}")
    # Coclasess in Wine's IDL often appear as plain 'coclass X { ... }' with the uuid in a
    # library attribute, so also print any literal CLSID_ definitions found nearby.
    for m in re.finditer(r"(CLSID_\w*InputProcessor\w*)[^\n]*\n([^\n]*)", text):
        print(f"[hint] {m.group(1)} -> {m.group(2).strip()[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
