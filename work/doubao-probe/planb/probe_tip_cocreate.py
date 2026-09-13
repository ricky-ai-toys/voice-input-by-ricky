"""Plan B: can we instantiate the vendor's TSF text service ourselves?

If CoCreateInstance on the Doubao IME text service CLSID works from a plain Python process,
we can *host* the vendor's own client logic (it already knows how to talk to the engine and
capture the microphone) instead of reimplementing the rpc client.

The CLSID is registered machine-wide by the official install, so this works locally today;
on a machine without the IME the same CLSID can be registered per-user (HKCU\\Software\\Classes),
which needs no admin rights.

    python probe_tip_cocreate.py
"""
import sys

import comtypes
import comtypes.client

CLSID_TIP = "{9D2B2E2B-3C93-4D2F-9D35-6EEB85F0D2B0}"
IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"


def main() -> int:
    comtypes.CoInitialize()
    print(f"[info] CoCreateInstance({CLSID_TIP}) ...")
    try:
        obj = comtypes.client.CreateObject(CLSID_TIP, interface=comtypes.IUnknown)
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] {type(exc).__name__}: {exc}")
        return 1
    print(f"[ok] instantiated: {obj}")

    # ask for the TSF text-input-processor interface (vtable slot 3 in ITSF)
    try:
        unknown = obj.QueryInterface(comtypes.IUnknown)
        print(f"[ok] QueryInterface(IUnknown) -> {unknown}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] QueryInterface failed: {exc}")

    try:
        obj.Release()
    except Exception:
        pass
    comtypes.CoUninitialize()
    return 0


if __name__ == "__main__":
    sys.exit(main())
