"""Plan B: let the vendor's own TSF core be the client that receives the transcript.

The engine only hands the recognised text to a client it treats as a real text service. Instead
of re-implementing that, this tool loads the *copy's* patched `tsf-oime-core.dll` directly
(`DllGetClassObject`, so no COM registration is touched), hosts a minimal TSF text store for it
(the plumbing from milestone 11), drives the voice session on the private engine, and then reads
the text the core inserted into the store.

    python core_host.py scratch [--hold 2] [--tail 8] [--wav path]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

import host_tip_harness as harness
from pipe_client import Pipe, pb_int, pb_str, start_server
from settings_ipc_client import SettingsPipe, request
from try_voice import lp, make_foreground_window, send_key
from tsf_activate import activate_doubao_for_process

IID_IClassFactory = "{00000001-0000-0000-C000-000000000046}"
CLSID_DOUBAO_TIP = harness.CLSID_DOUBAO_TIP
IID_ITfTextInputProcessor = harness.IID_ITfTextInputProcessor
FFPLAY = r"E:\ffmpeg\bin\ffplay.exe"
DEFAULT_WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "audio", "three_zh_16k.wav")

ole32 = ctypes.OleDLL("ole32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def load_tip_from_path(dll_path: str, verbose: bool = True) -> ctypes.c_void_p:
    """Create the TIP instance from a DLL path instead of the registered COM server."""
    kernel32.LoadLibraryExW.restype = wt.HMODULE
    kernel32.LoadLibraryExW.argtypes = [wt.LPCWSTR, wt.HANDLE, wt.DWORD]
    LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
    module = kernel32.LoadLibraryExW(dll_path, None, LOAD_WITH_ALTERED_SEARCH_PATH)
    if not module:
        raise OSError(f"LoadLibraryExW({dll_path}) failed err={ctypes.get_last_error()}")
    kernel32.GetProcAddress.restype = ctypes.c_void_p
    kernel32.GetProcAddress.argtypes = [wt.HMODULE, ctypes.c_char_p]
    proc = kernel32.GetProcAddress(module, b"DllGetClassObject")
    if not proc:
        raise OSError("DllGetClassObject not found")

    factory = ctypes.c_void_p()
    get_class_object = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p))(proc)
    clsid = harness.GUID.parse(CLSID_DOUBAO_TIP)
    iid = harness.GUID.parse(IID_IClassFactory)
    hr = get_class_object(ctypes.byref(clsid), ctypes.byref(iid), ctypes.byref(factory))
    if verbose:
        print(f"[core] DllGetClassObject hr=0x{hr & 0xFFFFFFFF:08X} factory={factory.value}")
    if not factory.value:
        raise OSError("no class factory")

    tip = ctypes.c_void_p()
    iid_tip = harness.GUID.parse(IID_ITfTextInputProcessor)
    hr = harness.vcall(factory.value, 3, ctypes.c_long,
                       [ctypes.c_void_p, ctypes.POINTER(harness.GUID),
                        ctypes.POINTER(ctypes.c_void_p)],
                       None, ctypes.byref(iid_tip), ctypes.byref(tip))
    if verbose:
        print(f"[core] IClassFactory::CreateInstance hr=0x{hr & 0xFFFFFFFF:08X} tip={tip.value}")
    return tip


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratch"))
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 2.0
    tail = float(sys.argv[sys.argv.index("--tail") + 1]) if "--tail" in sys.argv else 8.0
    wav = os.path.abspath(sys.argv[sys.argv.index("--wav") + 1]) if "--wav" in sys.argv else \
        os.path.abspath(DEFAULT_WAV)
    log_path = os.path.join(runtime, "pipe_client_server.log")

    proc = start_server(runtime)
    print(f"[info] engine pid={proc.pid}")
    sp = SettingsPipe()
    print(f"[arm] {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()

    ole32.CoInitialize(None)
    tm = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(harness.GUID.parse(harness.CLSID_TF_THREAD_MGR)),
                                None, 1,
                                ctypes.byref(harness.GUID.parse(harness.IID_ITfThreadMgr)),
                                ctypes.byref(tm))
    tid = wt.DWORD(0)
    harness.vcall(tm.value, 3, ctypes.c_long, [ctypes.POINTER(wt.DWORD)], ctypes.byref(tid))
    pdm = ctypes.c_void_p()
    harness.vcall(tm.value, 5, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                  ctypes.byref(pdm))
    print(f"[tsf] ThreadMgr hr=0x{hr & 0xFFFFFFFF:08X} tid={tid.value} docmgr={pdm.value}")

    hwnd = make_foreground_window()
    print(f"[host] hwnd=0x{hwnd:X} pid={os.getpid()}")
    store = harness.TextStoreACP(hwnd)
    pic = ctypes.c_void_p()
    edit_cookie = wt.DWORD(0)
    hr = harness.vcall(pdm.value, 3, ctypes.c_long,
                       [wt.DWORD, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wt.DWORD)],
                       tid.value, 0, store._self_ptr, ctypes.byref(pic),
                       ctypes.byref(edit_cookie))
    prev = ctypes.c_void_p()
    harness.vcall(tm.value, 9, ctypes.c_long,
                  [wt.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                  hwnd, pdm.value, ctypes.byref(prev))
    harness.vcall(tm.value, 8, ctypes.c_long, [ctypes.c_void_p], pdm.value)
    print(f"[tsf] CreateContext/AssociateFocus done hr=0x{hr & 0xFFFFFFFF:08X} pic={pic.value}")

    tip = load_tip_from_path(os.path.join(runtime, "tsf-oime-core.dll"))
    if tip.value:
        hr = harness.vcall(tip.value, 3, ctypes.c_long, [ctypes.c_void_p, wt.DWORD],
                           tm.value, tid.value)
        print(f"[core] ITfTextInputProcessor::Activate hr=0x{hr & 0xFFFFFFFF:08X}")
    print(f"[tsf] activate Doubao profile -> 0x{activate_doubao_for_process(verbose=False):08X}")

    pipe = Pipe(timeout=35.0)
    my_pid = os.getpid()
    for op, body, name in (
            (0x02, b"", "Activate"),
            (0x11, pb_str(1, "keyboard") + pb_str(2, "oime") + pb_str(3, "planb"), "ImeChanged"),
            (0x1B, bytes.fromhex("0801"), "SetUIElementShowState"),
            (0x0C, bytes.fromhex("08f8071801"), "SimpleMessage(1016,1)"),
            (0x06, my_pid.to_bytes(4, "little") + (1).to_bytes(4, "little") + lp("python.exe"),
             "FocusIn(pid)"),
            (0x15, int(hwnd).to_bytes(8, "little") + (0x1122334455667788).to_bytes(8, "little"),
             "RegisterTsfNotifySink"),
            (0x08, pb_int(1, 100) + pb_int(2, 100) + pb_int(3, 20), "SetCursorPos"),
            (0x14, lp("hello ") + lp(" world") + lp("") + int(hwnd).to_bytes(8, "little")
             + lp("planb") + lp("python.exe"), "UpdateHostContext")):
        res = pipe.call(op, body)
        print(f"[ctx] {name:22s} -> {res if res is None else res[2]}")

    player = None
    if os.path.exists(FFPLAY) and os.path.exists(wav):
        player = subprocess.Popen([FFPLAY, "-nodisp", "-autoexit", "-loglevel", "quiet", wav],
                                  creationflags=0x00000008)
    time.sleep(0.3)
    send_key(0xA5)
    print("[key] Right Alt down (voice session start)")
    deadline = time.time() + hold
    while time.time() < deadline:
        harness.pump_messages(0.2)
    send_key(0x41)          # unrelated key = clean stop while the hook still sees injected keys
    time.sleep(0.05)
    send_key(0x41, up=True)
    send_key(0xA5, up=True)
    print("[key] unrelated key 'A' -> PRESS_STOP, Right Alt up")
    if player:
        try:
            player.kill()
        except Exception:
            pass

    deadline = time.time() + tail
    while time.time() < deadline:
        harness.pump_messages(0.2)
        if store.text:
            print(f"   [store] {store.text!r}")

    print(f"[result] text store after the session: {store.text!r}")
    if os.path.exists(log_path):
        blob = open(log_path, "rb").read().decode("utf-8", "replace")
        for tag in ("voice startfrom", "PRESS_STOP", "slot_PeekVoiceCommit session=0 bytes=0",
                    "asr session started"):
            print(f"   [log] {tag}: {blob.count(tag)}")
    pipe.close()
    proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
