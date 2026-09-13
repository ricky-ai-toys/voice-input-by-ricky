"""Plan B: activate the vendor's TSF text service in our own process.

Sequence:
  1. CoCreateInstance(CLSID_TF_ThreadMgr) -> ITfThreadMgr, Activate()
  2. CreateDocumentMgr()
  3. CoCreateInstance(Doubao TIP CLSID) -> ITfTextInputProcessor(Ex)
  4. ActivateEx(thread_mgr, client_id, 0)

Run it through run_with_hooks.py so every CreateFileW in this process is logged - if the
service opens the engine pipe on activation, we finally see the client's transport.

    python run_with_hooks.py <runtime_dir> probe_activate_tip.py
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

import comtypes

CLSID_TF_THREAD_MGR = "{529A9E6B-6587-4F23-AB9E-9C7D683E3C50}"
CLSID_DOUBAO_TIP = "{9D2B2E2B-3C93-4D2F-9D35-6EEB85F0D2B0}"
IID_ITfThreadMgr = "{AA80E801-2021-11D2-93E0-0060B067B86E}"
IID_ITfTextInputProcessorEx = "{6E4EF1B2-BE96-4E0F-9E44-3B2A2C7D1B2D}"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, s: str) -> "GUID":
        return cls.from_buffer_copy(comtypes.GUID(s))


ole32 = ctypes.windll.ole32
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wt.DWORD,
                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long


def vcall(ptr: int, slot: int, restype, argtypes, *args):
    """Call a COM vtable method by slot index."""
    vtable = ctypes.c_void_p.from_address(ptr).value
    fn_ptr = ctypes.c_void_p.from_address(vtable + slot * 8).value
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(fn_ptr)(ptr, *args)


def main() -> int:
    comtypes.CoInitialize()

    tm = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.from_string(CLSID_TF_THREAD_MGR)), None, 1,
                                ctypes.byref(GUID.from_string(IID_ITfThreadMgr)), ctypes.byref(tm))
    print(f"[1] ITfThreadMgr hr=0x{hr & 0xFFFFFFFF:08X} ptr={tm.value}", flush=True)
    if hr < 0:
        return 1

    tid = wt.DWORD(0)
    hr = vcall(tm.value, 3, ctypes.c_long, [ctypes.POINTER(wt.DWORD)], ctypes.byref(tid))
    print(f"[2] ITfThreadMgr::Activate hr=0x{hr & 0xFFFFFFFF:08X} tid={tid.value}", flush=True)

    pdm = ctypes.c_void_p()
    hr = vcall(tm.value, 5, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(pdm))
    print(f"[3] CreateDocumentMgr hr=0x{hr & 0xFFFFFFFF:08X} ptr={pdm.value}", flush=True)

    tip = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.from_string(CLSID_DOUBAO_TIP)), None, 1,
                                ctypes.byref(GUID.from_string(IID_ITfTextInputProcessorEx)),
                                ctypes.byref(tip))
    print(f"[4] CoCreateInstance(ITfTextInputProcessorEx) hr=0x{hr & 0xFFFFFFFF:08X} ptr={tip.value}",
          flush=True)
    if hr < 0:
        # retry with the base interface (Activate instead of ActivateEx)
        hr = ole32.CoCreateInstance(ctypes.byref(GUID.from_string(CLSID_DOUBAO_TIP)), None, 1,
                                    ctypes.byref(GUID.from_string("{AA80E7F7-2021-11D2-93E0-0060B067B86E}")),
                                    ctypes.byref(tip))
        print(f"[4b] base ITfTextInputProcessor hr=0x{hr & 0xFFFFFFFF:08X} ptr={tip.value}", flush=True)
        if hr < 0:
            return 1
        hr = vcall(tip.value, 3, ctypes.c_long, [ctypes.c_void_p, wt.DWORD], tm.value, tid.value)
        print(f"[5] Activate hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)
    else:
        hr = vcall(tip.value, 5, ctypes.c_long, [ctypes.c_void_p, wt.DWORD, wt.DWORD],
                   tm.value, tid.value, 0)
        print(f"[5] ActivateEx hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)

    print("[6] keeping the service alive for 8s (watch for pipe opens)", flush=True)
    time.sleep(8)
    print("[done]", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
