"""Which microphone should the engine use?

The engine reads `voice.selectedMicrophoneId` from its config. A fresh machine has it empty, and
the endpoint ids look like `{0.0.1.00000000}.{guid}`. This module asks Windows (MMDeviceEnumerator)
for the default capture endpoint and for the list of active ones, so the app can fill the setting
in instead of letting the engine pick a dead endpoint and record silence.

    python audio_endpoints.py            # list active capture endpoints + the default
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

ole32 = ctypes.OleDLL("ole32")
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
ole32.CLSIDFromString.argtypes = [wt.LPCWSTR, ctypes.c_void_p]

CLSID_MM_DEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMM_DEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
CLSCTX_ALL = 0x17
EDATAFLOW_CAPTURE = 1
EROLES = ("eConsole", "eMultimedia", "eCommunications")
DEVICE_STATE_ACTIVE = 0x1
DEVICE_STATE_DISABLED = 0x2
DEVICE_STATE_NOTPRESENT = 0x4
DEVICE_STATE_UNPLUGGED = 0x8


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        g = cls()
        ole32.CLSIDFromString(text, ctypes.byref(g))
        return g


def vcall(ptr: int, slot: int, restype, argtypes, *args):
    vtable = ctypes.c_void_p.from_address(ptr).value
    fn = ctypes.c_void_p.from_address(vtable + slot * 8).value
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn)(ptr, *args)


def _device_id(device: int) -> str:
    out = ctypes.c_wchar_p()
    hr = vcall(device, 5, ctypes.c_long, [ctypes.POINTER(ctypes.c_wchar_p)], ctypes.byref(out))
    if hr != 0 or not out.value:
        return ""
    text = out.value
    ole32.CoTaskMemFree(out)
    return text


def _release(ptr: int) -> None:
    vcall(ptr, 2, ctypes.c_ulong, [])


def _enumerator() -> int:
    um = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_MM_DEVICE_ENUMERATOR)), None,
                                CLSCTX_ALL, ctypes.byref(GUID.parse(IID_IMM_DEVICE_ENUMERATOR)),
                                ctypes.byref(um))
    if hr != 0 or not um.value:
        raise OSError(f"CoCreateInstance(MMDeviceEnumerator) hr=0x{hr & 0xFFFFFFFF:08X}")
    return um.value


def default_capture_id(role: int = 0) -> str:
    """Endpoint id of the default recording device ("" when there is none)."""
    um = _enumerator()
    try:
        device = ctypes.c_void_p()
        hr = vcall(um, 4, ctypes.c_long, [ctypes.c_int, ctypes.c_int,
                                          ctypes.POINTER(ctypes.c_void_p)],
                   EDATAFLOW_CAPTURE, role, ctypes.byref(device))
        if hr != 0 or not device.value:
            return ""
        try:
            return _device_id(device.value)
        finally:
            _release(device.value)
    finally:
        _release(um)


def active_capture_ids() -> list[tuple[str, int]]:
    """[(endpoint id, state)] for every *active* capture endpoint."""
    um = _enumerator()
    out: list[tuple[str, int]] = []
    try:
        coll = ctypes.c_void_p()
        hr = vcall(um, 3, ctypes.c_long, [ctypes.c_int, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)],
                   EDATAFLOW_CAPTURE, DEVICE_STATE_ACTIVE, ctypes.byref(coll))
        if hr != 0 or not coll.value:
            return out
        try:
            count = ctypes.c_uint(0)
            vcall(coll.value, 3, ctypes.c_long, [ctypes.POINTER(ctypes.c_uint)],
                  ctypes.byref(count))
            for i in range(count.value):
                device = ctypes.c_void_p()
                hr = vcall(coll.value, 4, ctypes.c_long, [ctypes.c_uint,
                                                          ctypes.POINTER(ctypes.c_void_p)],
                           i, ctypes.byref(device))
                if hr != 0 or not device.value:
                    continue
                try:
                    state = ctypes.c_uint(0)
                    vcall(device.value, 6, ctypes.c_long, [ctypes.POINTER(ctypes.c_uint)],
                          ctypes.byref(state))
                    out.append((_device_id(device.value), state.value))
                finally:
                    _release(device.value)
        finally:
            _release(coll.value)
    finally:
        _release(um)
    return out


def main() -> int:
    ole32.CoInitializeEx(None, 0)
    for role, name in enumerate(EROLES):
        print(f"default ({name}): {default_capture_id(role)}")
    print("active capture endpoints:")
    for endpoint, state in active_capture_ids():
        print(f"   state={state}  {endpoint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
