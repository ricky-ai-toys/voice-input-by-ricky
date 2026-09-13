"""Plan B: one *reference* session against the installed IME, to capture what a real client gets.

The engine only hands the transcript to the vendor's own TSF core. So run that core in our
process (hosted text store from milestone 11), point it at the *installed* engine (the normal
pipe), and make that engine accept a synthetic hotkey for the duration of this run:

* patch its "ignore injected keys" filter **in memory** with frida - the file on disk is not
  touched and the original bytes are written back before we detach;
* arm its own settings channel (`setVoiceTryoutActive`), which is what the settings UI does;
* inject Right Alt, hold, release, and read whatever the core inserts into our text store.

Everything is reverted at the end (bytes, tryout state, keys), and keys are always released.

    python reference_session.py [--hold 5] [--tail 12]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import frida

import host_tip_harness as harness
from commit_probe import release_all
from settings_ipc_client import SettingsPipe, request
from try_voice import send_key

OFFICIAL_PIPE = "\\\\.\\pipe\\DoubaoIme\\settings-rpc"

# the two places the engine drops injected input (see patch_voicehook.py)
FILTERS = [
    (0x742743, bytes.fromhex("41f64008107511"), b"\x90" * 7),
    (0x742DBD, bytes.fromhex("f64708100f8513010000"), bytes.fromhex("f6470810") + b"\x90" * 6),
]

JS = r"""
const exe = Process.getModuleByName('ImeService.exe');
rpc.exports = {
  read(off, len) {
    return Array.from(new Uint8Array(exe.base.add(off).readByteArray(len)));
  },
  write(off, bytes) {
    exe.base.add(off).writeByteArray(bytes);
    return true;
  }
};
send({ kind: 'ready', base: exe.base.toString() });
"""


def find_official_engine() -> int | None:
    for proc in frida.get_local_device().enumerate_processes():
        if proc.name.lower() != "imeservice.exe":
            continue
        try:
            session = frida.attach(proc.pid)
        except Exception:
            continue
        try:
            result = {}
            script = session.create_script(
                "send({exe: (Process.findModuleByName('ImeService.exe')||{}).path||null});")
            script.on("message", lambda m, d: result.update(m.get("payload") or {}))
            script.load()
            path = (result.get("exe") or "")
            session.detach()
            # the process may not expose its path to us; a copy would live under doubao-probe
            if not path or "doubao-probe" not in path.lower():
                return proc.pid
        except Exception:
            try:
                session.detach()
            except Exception:
                pass
    return None


def main() -> int:
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 5.0
    tail = float(sys.argv[sys.argv.index("--tail") + 1]) if "--tail" in sys.argv else 12.0
    pid = find_official_engine()
    if not pid:
        print("[fail] the installed ImeService.exe is not running")
        return 1
    print(f"[info] installed engine pid={pid}")

    session_fr = frida.attach(pid)
    script = session_fr.create_script(JS)
    ready = {"ok": False}
    script.on("message", lambda m, d: ready.update(ok=True) if m.get("type") == "send" else None)
    script.load()
    while not ready["ok"]:
        time.sleep(0.05)
    print("[hook] attached to the installed engine")

    originals: list[tuple[int, bytes]] = []
    for offset, expected, patch in FILTERS:
        current = bytes(script.exports_sync.read(offset, len(expected)))
        if current != expected:
            print(f"[warn] 0x{offset:X} does not look like the expected filter "
                  f"({current.hex()}) - skipping")
            continue
        script.exports_sync.write(offset, list(patch))
        originals.append((offset, expected))
        print(f"[hook] injected-key filter relaxed at 0x{offset:X} (memory only)")

    sp = SettingsPipe(OFFICIAL_PIPE)
    print(f"[arm] tryout -> {sp.call(request('settings.setVoiceTryoutActive', {'active': True, 'cookie': int(time.time())}))}")
    sp.close()

    try:
        ole32 = ctypes.OleDLL("ole32")
        ole32.CoInitialize(None)
        tm = ctypes.c_void_p()
        ole32.CoCreateInstance(ctypes.byref(harness.GUID.parse(harness.CLSID_TF_THREAD_MGR)),
                               None, 1,
                               ctypes.byref(harness.GUID.parse(harness.IID_ITfThreadMgr)),
                               ctypes.byref(tm))
        tid = wt.DWORD(0)
        harness.vcall(tm.value, 3, ctypes.c_long, [ctypes.POINTER(wt.DWORD)],
                      ctypes.byref(tid))
        pdm = ctypes.c_void_p()
        harness.vcall(tm.value, 5, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                      ctypes.byref(pdm))
        hwnd = harness.create_host_window()
        harness.focus_window(hwnd)
        store = harness.TextStoreACP(hwnd)
        pic = ctypes.c_void_p()
        edit_cookie = wt.DWORD(0)
        harness.vcall(pdm.value, 3, ctypes.c_long,
                      [wt.DWORD, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                       ctypes.POINTER(wt.DWORD)],
                      tid.value, 0, store._self_ptr, ctypes.byref(pic),
                      ctypes.byref(edit_cookie))
        prev = ctypes.c_void_p()
        harness.vcall(tm.value, 9, ctypes.c_long,
                      [wt.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                      hwnd, pdm.value, ctypes.byref(prev))
        harness.vcall(tm.value, 8, ctypes.c_long, [ctypes.c_void_p], pdm.value)
        tip = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(harness.GUID.parse(harness.CLSID_DOUBAO_TIP)),
                                    None, 1,
                                    ctypes.byref(harness.GUID.parse(harness.IID_ITfTextInputProcessor)),
                                    ctypes.byref(tip))
        print(f"[tsf] installed TIP hr=0x{hr & 0xFFFFFFFF:08X} tip={tip.value}")
        if tip.value:
            hr = harness.vcall(tip.value, 3, ctypes.c_long, [ctypes.c_void_p, wt.DWORD],
                               tm.value, tid.value)
            print(f"[tsf] TIP Activate hr=0x{hr & 0xFFFFFFFF:08X}")
        harness.vcall(tm.value, 9, ctypes.c_long,
                      [wt.HWND, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
                      hwnd, pdm.value, ctypes.byref(prev))
        harness.pump_messages(1.0)

        send_key(0xA5)
        print("[key] Right Alt down (reference session on the installed engine)")
        deadline = time.time() + hold
        while time.time() < deadline:
            harness.pump_messages(0.2)
            if store.text:
                print(f"   [store] {store.text!r}")
        send_key(0xA5, up=True)
        print("[key] Right Alt up")
        deadline = time.time() + tail
        while time.time() < deadline:
            harness.pump_messages(0.2)
            if store.text:
                print(f"   [store] {store.text!r}")
        print(f"[result] text store = {store.text!r}")
    finally:
        for offset, original in originals:
            script.exports_sync.write(offset, list(original))
            print(f"[hook] restored 0x{offset:X}")
        try:
            sp2 = SettingsPipe(OFFICIAL_PIPE)
            sp2.call(request("settings.setVoiceTryoutActive",
                             {"active": False, "cookie": int(time.time())}))
            sp2.close()
            print("[arm] tryout disarmed")
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] could not disarm tryout: {exc}")
        send_key(0xA5, up=True)
        try:
            session_fr.detach()
        except Exception:
            pass
        release_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
