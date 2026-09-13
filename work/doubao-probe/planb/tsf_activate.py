"""Plan B: make the engine see our process as an app that has Doubao IME selected.

`VoiceKeyHookProc` matches the hotkey but then refuses:

    [VHK][Trig] ACTIVATION but not allowed (ime not foreground-active), pass through

The per-process way to change that is the same thing a TSF-aware app does when it switches
input method: activate the Doubao profile for *this* process (TF_IPPMF_FORPROCESS) and let the
TIP substitute its keyboard layout. No admin rights, no machine-wide state.

    python tsf_activate.py            # activate for this process and report hr values
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

ole32 = ctypes.OleDLL("ole32")
user32 = ctypes.WinDLL("user32", use_last_error=True)
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CLSIDFromString.argtypes = [wt.LPCWSTR, ctypes.c_void_p]
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        g = cls()
        ole32.CLSIDFromString(s, ctypes.byref(g))
        return g


def vcall(ptr: int, slot: int, restype, argtypes, *args):
    vtable = ctypes.c_void_p.from_address(ptr).value
    fn = ctypes.c_void_p.from_address(vtable + slot * 8).value
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn)(ptr, *args)


CLSID_TF_THREAD_MGR = "{529A9E6B-6587-4F23-AB9E-9C7D683E3C50}"
IID_ITfThreadMgr = "{AA80E801-2021-11D2-93E0-0060B067B86E}"
CLSID_TF_INPUT_PROCESSOR_PROFILES = "{33C53A50-F456-4884-B049-85FD643ECFED}"
IID_ITfInputProcessorProfileMgr = "{71C6E74C-0F28-11D8-A82A-00065B84435C}"
CLSID_DOUBAO_TIP = "{9D2B2E2B-3C93-4D2F-9D35-6EEB85F0D2B0}"
IID_ITfTextInputProcessor = "{AA80E7F7-2021-11D2-93E0-0060B067B86E}"
DOUBAO_PROFILE = "{2B4D4B3A-4D4F-4C0A-8E66-7F771A2B9C10}"
LANGID_CHS = 0x0804
TF_PROFILETYPE_INPUTPROCESSOR = 0x1
TF_IPPMF_FORPROCESS = 0x10000000

CLSCTX_INPROC_SERVER = 0x1


def activate_doubao_for_process(verbose: bool = True) -> int:
    """Returns 0 when the profile activation reported success."""
    hr_init = ole32.CoInitializeEx(None, 2)      # COINIT_APARTMENTTHREADED
    if verbose and hr_init not in (0, 1):        # S_OK / S_FALSE
        print(f"[tsf] CoInitializeEx hr=0x{hr_init & 0xFFFFFFFF:08X}")
    tm = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_TF_THREAD_MGR)), None,
                                CLSCTX_INPROC_SERVER, ctypes.byref(GUID.parse(IID_ITfThreadMgr)),
                                ctypes.byref(tm))
    if verbose:
        print(f"[tsf] CoCreateInstance(ThreadMgr) hr=0x{hr & 0xFFFFFFFF:08X}")
    tid = wt.DWORD(0)
    if tm.value:
        hr = vcall(tm.value, 3, ctypes.c_long, [ctypes.POINTER(wt.DWORD)], ctypes.byref(tid))
        if verbose:
            print(f"[tsf] ThreadMgr::Activate hr=0x{hr & 0xFFFFFFFF:08X} tid={tid.value}")

    pmgr = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_TF_INPUT_PROCESSOR_PROFILES)),
                                None, CLSCTX_INPROC_SERVER,
                                ctypes.byref(GUID.parse(IID_ITfInputProcessorProfileMgr)),
                                ctypes.byref(pmgr))
    if verbose:
        print(f"[tsf] CoCreateInstance(ProfileMgr) hr=0x{hr & 0xFFFFFFFF:08X} ptr={pmgr.value}")
    if not pmgr.value:
        return 1
    clsid = GUID.parse(CLSID_DOUBAO_TIP)
    profile = GUID.parse(DOUBAO_PROFILE)
    hr = vcall(pmgr.value, 3, ctypes.c_long,
               [wt.DWORD, wt.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
                ctypes.c_void_p, wt.DWORD],
               TF_PROFILETYPE_INPUTPROCESSOR, LANGID_CHS, ctypes.byref(clsid),
               ctypes.byref(profile), None, TF_IPPMF_FORPROCESS)
    if verbose:
        print(f"[tsf] ActivateProfile(doubao, 0x{LANGID_CHS:04X}, FORPROCESS) "
              f"hr=0x{hr & 0xFFFFFFFF:08X}")
    hkl = user32.LoadKeyboardLayoutW("00000804", 0x00000001)
    old = user32.ActivateKeyboardLayout(hkl, 0) if hkl else None
    if verbose:
        print(f"[tsf] LoadKeyboardLayout('00000804')=0x{hkl:X} -> old=0x{old if old else 0:X}")

    # Load the vendor TIP into this process as well: the engine's IME manager asks TSF which
    # text service is live for the foreground thread, and a process that never instantiated
    # the TIP still reports "keyboard".
    tip = ctypes.c_void_p()
    hr_tip = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_DOUBAO_TIP)), None,
                                    CLSCTX_INPROC_SERVER,
                                    ctypes.byref(GUID.parse(IID_ITfTextInputProcessor)),
                                    ctypes.byref(tip))
    if verbose:
        print(f"[tsf] CoCreateInstance(Doubao TIP) hr=0x{hr_tip & 0xFFFFFFFF:08X} ptr={tip.value}")
    if tip.value and tm.value:
        hr_act = vcall(tip.value, 3, ctypes.c_long, [ctypes.c_void_p, wt.DWORD],
                       tm.value, tid.value)
        if verbose:
            print(f"[tsf] TIP::Activate hr=0x{hr_act & 0xFFFFFFFF:08X}")
    return 0 if hr == 0 else hr & 0xFFFFFFFF


def main() -> int:
    rc = activate_doubao_for_process()
    print(f"[tsf] result=0x{rc:08X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
