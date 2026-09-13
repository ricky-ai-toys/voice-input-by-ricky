"""Patch ImeService.exe's manifest so a standard user can start it.

The vendor manifest requests uiAccess="true", which makes CreateProcess fail
with ERROR_ELEVATION_REQUIRED (740) for non-elevated callers. XML booleans also
accept "0"/"1", and the replacement is byte-length neutral, so the resource can
be rewritten in place without touching the PE resource directory.
"""
import os
import sys

OLD = b'uiAccess="true"'
NEW = b'uiAccess="false"'          # +1 byte
# reclaim exactly one byte from the redundant CR of the CRLF after the XML
# declaration so the manifest resource keeps its original size
SHRINK_OLD = b'?>\r\n<assembly'
SHRINK_NEW = b'?>\n<assembly'


def patch(path: str) -> int:
    with open(path, "rb") as fh:
        data = fh.read()
    count = data.count(OLD)
    if count == 0:
        print(f"{path}: no manifest patch needed (already patched?)")
        return 0
    patched = data.replace(OLD, NEW)
    if len(patched) != len(data):
        if SHRINK_OLD not in patched:
            print(f"{path}: cannot reclaim a byte for the longer boolean")
            return 0
        patched = patched.replace(SHRINK_OLD, SHRINK_NEW, 1)
    if len(patched) != len(data):
        print(f"{path}: size changed ({len(data)} -> {len(patched)}), aborting")
        return 0
    backup = path + ".orig"
    if not os.path.exists(backup):
        with open(backup, "wb") as fh:
            fh.write(data)
    with open(path, "wb") as fh:
        fh.write(patched)
    print(f"{path}: patched {count} occurrence(s); backup at {backup}")
    return count


if __name__ == "__main__":
    total = 0
    for target in sys.argv[1:]:
        total += patch(target)
    sys.exit(0 if total >= 0 else 1)
