"""Plan B: host the Doubao TSF text service and drive voice input from Python.

Steps:
  1. implement a minimal ITextStoreACP (in-memory text) with ctypes vtables
  2. ITfThreadMgr Activate -> CreateDocumentMgr -> CreateContext(our store) -> SetFocus
  3. CoCreateInstance(Doubao TIP) -> ITfTextInputProcessor::Activate
  4. QueryInterface(ITfKeyEventSink) and feed VK_RMENU down/up (the IME's push-to-talk key)

Everything is user-level; on a machine without the official IME the same CLSID can be
registered per-user and pointed at our portable copy.

    python host_tip_harness.py [seconds-to-hold]
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

S_OK = 0
S_FALSE = 1
E_NOTIMPL = -2147467263  # 0x80004001
E_NOINTERFACE = -2147467262  # 0x80004002

CLSID_TF_THREAD_MGR = "{529A9E6B-6587-4F23-AB9E-9C7D683E3C50}"
IID_ITfThreadMgr = "{AA80E801-2021-11D2-93E0-0060B067B86E}"
CLSID_DOUBAO_TIP = "{9D2B2E2B-3C93-4D2F-9D35-6EEB85F0D2B0}"
IID_ITfTextInputProcessor = "{AA80E7F7-2021-11D2-93E0-0060B067B86E}"
IID_ITfKeyEventSink = "{AA80E7F5-2021-11D2-93E0-0060B067B86E}"
IID_ITextStoreACP = "{28888FE3-C2A0-483A-A3EA-8CB1CE51FF3E}"

VK_RMENU = 0xA5
TS_LF_READWRITE = 0x6
TS_SS_DISJOINTSEL = 0x1


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        g = cls()
        ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
        return g

    def to_string(self) -> str:
        d4 = "".join(f"{b:02X}" for b in self.Data4)
        return f"{{{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-{d4[:4]}-{d4[4:]}}}"


class TS_SELECTION_ACP(ctypes.Structure):
    _fields_ = [("acpStart", ctypes.c_long), ("acpEnd", ctypes.c_long),
                ("style_ase", wt.DWORD), ("style_interim", ctypes.c_long)]


class TS_TEXTCHANGE(ctypes.Structure):
    _fields_ = [("acpStart", ctypes.c_long), ("acpOldEnd", ctypes.c_long),
                ("acpNewEnd", ctypes.c_long)]


class TS_STATUS(ctypes.Structure):
    _fields_ = [("dwDynamicFlags", wt.DWORD), ("dwStaticFlags", wt.DWORD)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


ole32 = ctypes.windll.ole32
ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wt.DWORD,
                                   ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CLSIDFromString.argtypes = [wt.LPCWSTR, ctypes.POINTER(GUID)]
user32 = ctypes.windll.user32


class TextStoreACP:
    """Minimal ITextStoreACP: an in-memory UTF-16 string plus a selection."""

    def __init__(self) -> None:
        self.text = ""
        self.sel = (0, 0)
        self.sink = None
        self.lock_flags = 0
        self._refs = 1
        self._self_ptr = None
        self._vtable = None
        self._keep = []  # keep callbacks alive
        self._build_vtable()
        self.log: list[str] = []

    # -- plumbing ----------------------------------------------------------- #
    def _hook(self, index: int, fn) -> None:
        proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *fn[1])
        cb = proto(fn[0])
        self._keep.append(cb)
        base = ctypes.cast(self._vtable, ctypes.c_void_p).value
        ctypes.c_void_p.from_address(base + index * 8).value = ctypes.cast(cb, ctypes.c_void_p).value

    def _build_vtable(self) -> None:
        size = (3 + 26) * ctypes.sizeof(ctypes.c_void_p)
        self._vtable = ctypes.create_string_buffer(size)
        base = ctypes.cast(self._vtable, ctypes.c_void_p).value
        # NOTE: the callbacks must be kept alive, otherwise the vtable holds dangling
        # pointers and TSF fail-fasts the whole process when it invokes an unset method.
        def notimpl(this):  # noqa: ANN001
            return E_NOTIMPL

        notimpl_proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
        for i in range(3 + 26):
            cb = notimpl_proto(notimpl)
            self._keep.append(cb)
            ctypes.c_void_p.from_address(base + i * 8).value = ctypes.cast(cb, ctypes.c_void_p).value

        def qi(this, riid, ppv):
            want = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
            got = want.to_string().upper()
            print(f"[qi] QueryInterface({got})", flush=True)
            if got in (IID_ITextStoreACP.upper(), "{00000000-0000-0000-C000-000000000046}"):
                ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = self._self_ptr
                return S_OK
            ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = None
            return E_NOINTERFACE

        def addref(this):
            self._refs += 1
            return self._refs

        def release(this):
            self._refs -= 1
            return self._refs

        self._hook(0, (qi, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]))
        self._hook(1, (addref, []))
        self._hook(2, (release, []))

        def advise_sink(this, punk, mask):
            self.sink = punk
            self.log.append(f"AdviseSink(mask={mask})")
            return S_OK

        def request_lock(this, flags, phr_session):
            self.lock_flags = flags
            if self.sink:
                vtable = ctypes.c_void_p.from_address(self.sink).value
                fn = ctypes.c_void_p.from_address(vtable + 8 * 8).value  # OnLockGranted
                proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wt.DWORD)
                proto(fn)(self.sink, flags)
            return S_OK

        def get_status(this, pdcs):
            st = ctypes.cast(pdcs, ctypes.POINTER(TS_STATUS)).contents
            st.dwDynamicFlags = 0
            st.dwStaticFlags = 0x10000000  # TS_S_ASYNC
            return S_OK

        def get_selection(this, index, count, psel, fetched):
            if index > 0:
                ctypes.cast(fetched, ctypes.POINTER(wt.ULONG))[0] = 0
                return S_OK
            if count > 0:
                arr = ctypes.cast(psel, ctypes.POINTER(TS_SELECTION_ACP))
                arr[0].acpStart = self.sel[0]
                arr[0].acpEnd = self.sel[1]
                arr[0].style_ase = TS_SS_DISJOINTSEL
                arr[0].style_interim = 0
            ctypes.cast(fetched, ctypes.POINTER(wt.ULONG))[0] = 1
            return S_OK

        def set_selection(this, count, psel):
            arr = ctypes.cast(psel, ctypes.POINTER(TS_SELECTION_ACP))
            self.sel = (arr[0].acpStart, arr[0].acpEnd)
            return S_OK

        def get_text(this, start, end, pch, cch_req, pcch_ret, runs, c_run_req, pc_run_ret, next_acp):
            data = self._slice(start, end)
            out = ctypes.cast(pch, ctypes.POINTER(wt.WCHAR)) if pch else None
            n = min(len(data), cch_req) if cch_req else 0
            for i in range(n):
                out[i] = data[i]
            if pcch_ret:
                ctypes.cast(pcch_ret, ctypes.POINTER(wt.ULONG))[0] = n
            if pc_run_ret:
                ctypes.cast(pc_run_ret, ctypes.POINTER(wt.ULONG))[0] = 0
            if next_acp:
                ctypes.cast(next_acp, ctypes.POINTER(ctypes.c_long))[0] = start + n
            return S_OK

        def set_text(this, flags, start, end, pch, cch, change):
            new = ctypes.wstring_at(pch, cch) if pch and cch else ""
            start = max(0, min(start, len(self.text)))
            end = max(start, min(end, len(self.text)))
            self.text = self.text[:start] + new + self.text[end:]
            self.sel = (start + len(new), start + len(new))
            self.log.append(f"SetText({new!r})")
            if change:
                ch = ctypes.cast(change, ctypes.POINTER(TS_TEXTCHANGE)).contents
                ch.acpStart = start
                ch.acpOldEnd = end
                ch.acpNewEnd = start + len(new)
            return S_OK

        def insert_at_selection(this, flags, pch, cch, pacp_start, pacp_end, change):
            new = ctypes.wstring_at(pch, cch) if pch and cch else ""
            at = self.sel[0]
            self.text = self.text[:at] + new + self.text[at:]
            self.sel = (at + len(new), at + len(new))
            self.log.append(f"InsertAtSelection({new!r})")
            if pacp_start:
                ctypes.cast(pacp_start, ctypes.POINTER(ctypes.c_long))[0] = at
            if pacp_end:
                ctypes.cast(pacp_end, ctypes.POINTER(ctypes.c_long))[0] = at + len(new)
            return S_OK

        def query_insert(this, start, end, cch, ps, pe):
            if ps:
                ctypes.cast(ps, ctypes.POINTER(ctypes.c_long))[0] = 0
            if pe:
                ctypes.cast(pe, ctypes.POINTER(ctypes.c_long))[0] = len(self.text)
            return S_OK

        def get_end_acp(this, pacp):
            ctypes.cast(pacp, ctypes.POINTER(ctypes.c_long))[0] = len(self.text)
            return S_OK

        def get_active_view(this, pview):
            ctypes.cast(pview, ctypes.POINTER(wt.DWORD))[0] = 1
            return S_OK

        def get_acp_from_point(this, pview, pt, flags, pacp):
            if pacp:
                ctypes.cast(pacp, ctypes.POINTER(ctypes.c_long))[0] = self.sel[0]
            return S_OK

        def get_text_ext(this, pview, start, end, prc, clipped):
            rc = ctypes.cast(prc, ctypes.POINTER(RECT)).contents
            rc.left, rc.top, rc.right, rc.bottom = 100, 100, 200, 120
            if clipped:
                ctypes.cast(clipped, ctypes.POINTER(wt.BOOL))[0] = 0
            return S_OK

        def get_screen_ext(this, pview, prc):
            rc = ctypes.cast(prc, ctypes.POINTER(RECT)).contents
            rc.left, rc.top, rc.right, rc.bottom = 0, 0, 1920, 1080
            return S_OK

        def get_wnd(this, pview, phwnd):
            hwnd = ctypes.c_void_p()
            user32.GetCursorPos(ctypes.byref(wt.POINT()))
            ctypes.cast(phwnd, ctypes.POINTER(ctypes.c_void_p))[0] = None
            return S_OK

        # ITextStoreACP slots start at index 3
        self._hook(3, (advise_sink, [ctypes.c_void_p, wt.DWORD]))
        self._hook(4, (lambda this, punk: S_OK, [ctypes.c_void_p]))
        self._hook(5, (request_lock, [wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]))
        self._hook(6, (get_status, [ctypes.POINTER(TS_STATUS)]))
        self._hook(7, (query_insert, [ctypes.c_long, ctypes.c_long, wt.ULONG,
                                      ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_long)]))
        self._hook(8, (get_selection, [wt.ULONG, wt.ULONG, ctypes.c_void_p,
                                       ctypes.POINTER(wt.ULONG)]))
        self._hook(9, (set_selection, [wt.ULONG, ctypes.c_void_p]))
        self._hook(10, (get_text, [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, wt.ULONG,
                                   ctypes.POINTER(wt.ULONG), ctypes.c_void_p, wt.ULONG,
                                   ctypes.POINTER(wt.ULONG), ctypes.POINTER(ctypes.c_long)]))
        self._hook(11, (set_text, [wt.DWORD, ctypes.c_long, ctypes.c_long, ctypes.c_void_p,
                                   wt.ULONG, ctypes.POINTER(TS_TEXTCHANGE)]))
        self._hook(16, (insert_at_selection, [wt.DWORD, ctypes.c_void_p, wt.ULONG,
                                              ctypes.POINTER(ctypes.c_long),
                                              ctypes.POINTER(ctypes.c_long),
                                              ctypes.POINTER(TS_TEXTCHANGE)]))
        self._hook(23, (get_end_acp, [ctypes.POINTER(ctypes.c_long)]))
        self._hook(24, (get_active_view, [ctypes.POINTER(wt.DWORD)]))
        self._hook(25, (get_acp_from_point, [wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                             ctypes.POINTER(ctypes.c_long)]))
        self._hook(26, (get_text_ext, [wt.DWORD, ctypes.c_long, ctypes.c_long,
                                       ctypes.POINTER(RECT), ctypes.POINTER(wt.BOOL)]))
        self._hook(27, (get_screen_ext, [wt.DWORD, ctypes.POINTER(RECT)]))
        self._hook(28, (get_wnd, [wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]))

        # COM object layout: the object's first field is a pointer to the vtable.
        # (handing the vtable array itself to COM makes TSF read a callback address as the
        #  vtable pointer and fail-fast - that was the crash)
        self._object = ctypes.create_string_buffer(8)
        obj_addr = ctypes.cast(self._object, ctypes.c_void_p).value
        ctypes.c_void_p.from_address(obj_addr).value = ctypes.cast(
            self._vtable, ctypes.c_void_p).value
        self._self_ptr = obj_addr

    def _slice(self, start: int, end: int) -> str:
        start = max(0, min(start, len(self.text)))
        end = len(self.text) if end < 0 else max(start, min(end, len(self.text)))
        return self.text[start:end]


def vcall(ptr: int, slot: int, restype, argtypes, *args):
    vtable = ctypes.c_void_p.from_address(ptr).value
    fn = ctypes.c_void_p.from_address(vtable + slot * 8).value
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn)(ptr, *args)


def main() -> int:
    hold = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    ole32.CoInitialize(None)

    tm = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_TF_THREAD_MGR)), None, 1,
                                ctypes.byref(GUID.parse(IID_ITfThreadMgr)), ctypes.byref(tm))
    print(f"[1] ITfThreadMgr hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)
    tid = wt.DWORD(0)
    vcall(tm.value, 3, ctypes.c_long, [ctypes.POINTER(wt.DWORD)], ctypes.byref(tid))
    pdm = ctypes.c_void_p()
    vcall(tm.value, 5, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(pdm))
    print(f"[2] tid={tid.value} pdm={pdm.value}", flush=True)
    # sanity check: is pdm really an ITfDocumentMgr?
    dm_vtable = ctypes.c_void_p.from_address(pdm.value).value
    check = ctypes.c_void_p()
    hr = vcall(pdm.value, 0, ctypes.c_long, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
               ctypes.byref(GUID.parse("{AA80E7F4-2021-11D2-93E0-0060B067B86E}")), ctypes.byref(check))
    print(f"[2.2] docmgr vtable=0x{dm_vtable:X} QI(ITfDocumentMgr) hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)

    store = TextStoreACP()
    print(f"[2.5] text store built, vtable=0x{store._self_ptr:X}", flush=True)
    raw = [ctypes.c_void_p.from_address(store._self_ptr + i * 8).value for i in range(4)]
    print("[2.55] vtable[0..3] = " + ", ".join(f"0x{v:X}" if v else "0" for v in raw), flush=True)
    # self-test the COM plumbing before handing it to TSF
    ppv = ctypes.c_void_p()
    hr_qi = vcall(store._self_ptr, 0, ctypes.c_long,
                  [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
                  ctypes.byref(GUID.parse(IID_ITextStoreACP)), ctypes.byref(ppv))
    end_acp = ctypes.c_long(-1)
    hr_end = vcall(store._self_ptr, 23, ctypes.c_long, [ctypes.POINTER(ctypes.c_long)],
                   ctypes.byref(end_acp))
    print(f"[2.6] store self-test: QI hr=0x{hr_qi & 0xFFFFFFFF:08X} ptr={ppv.value} "
          f"GetEndACP hr=0x{hr_end & 0xFFFFFFFF:08X} end={end_acp.value}", flush=True)
    pic = ctypes.c_void_p()
    edit_cookie = wt.DWORD(0)
    # ITfDocumentMgr::CreateContext(TfClientId tidOwner, DWORD dwFlags, IUnknown *punk,
    #                               ITfContext **ppic, TfEditCookie *pecTextStore)
    hr = vcall(pdm.value, 3, ctypes.c_long,
               [wt.DWORD, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wt.DWORD)],
               tid.value, 0, store._self_ptr, ctypes.byref(pic), ctypes.byref(edit_cookie))
    print(f"[3] CreateContext done", flush=True)
    print(f"[3] CreateContext(store) hr=0x{hr & 0xFFFFFFFF:08X} pic={pic.value}", flush=True)

    hr = vcall(tm.value, 8, ctypes.c_long, [ctypes.c_void_p], pdm.value)
    print(f"[4] SetFocus(docmgr) hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)

    tip = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(GUID.parse(CLSID_DOUBAO_TIP)), None, 1,
                                ctypes.byref(GUID.parse(IID_ITfTextInputProcessor)),
                                ctypes.byref(tip))
    print(f"[5] CoCreateInstance(TIP) hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)
    hr = vcall(tip.value, 3, ctypes.c_long, [ctypes.c_void_p, wt.DWORD], tm.value, tid.value)
    print(f"[6] Activate hr=0x{hr & 0xFFFFFFFF:08X}", flush=True)

    kev = ctypes.c_void_p()
    hr = vcall(tip.value, 0, ctypes.c_long, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)],
               ctypes.byref(GUID.parse(IID_ITfKeyEventSink)), ctypes.byref(kev))
    print(f"[7] QI(ITfKeyEventSink) hr=0x{hr & 0xFFFFFFFF:08X} ptr={kev.value}", flush=True)

    # The service does not expose ITfKeyEventSink; the core very likely reads the
    # push-to-talk key through a low-level keyboard hook inside this process, so also try
    # synthesized Right Alt keystrokes.
    print("[7.5] sending synthesized Right Alt (low-level hook path) ...", flush=True)
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_RMENU, 0x38, KEYEVENTF_EXTENDEDKEY, None)
    print("[7.6] Right Alt down, holding ...", flush=True)
    time.sleep(hold)
    user32.keybd_event(VK_RMENU, 0x38, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, None)
    print("[7.7] Right Alt up", flush=True)
    time.sleep(4)

    if kev.value:
        eaten = wt.BOOL(0)
        lparam_down = 0x00380001          # scan code 0x38, extended
        for label, slot in (("OnTestKeyDown", 4), ("OnKeyDown", 6)):
            hr = vcall(kev.value, slot, ctypes.c_long,
                       [ctypes.c_void_p, wt.WPARAM, wt.LPARAM, ctypes.POINTER(wt.BOOL)],
                       pic.value, VK_RMENU, lparam_down, ctypes.byref(eaten))
            print(f"[8] {label}(VK_RMENU) hr=0x{hr & 0xFFFFFFFF:08X} eaten={bool(eaten.value)}",
                  flush=True)
        print(f"[9] holding Right Alt for {hold:.0f}s (speak now if the mic is live) ...",
              flush=True)
        time.sleep(hold)
        lparam_up = 0xC0380001
        hr = vcall(kev.value, 7, ctypes.c_long,
                   [ctypes.c_void_p, wt.WPARAM, wt.LPARAM, ctypes.POINTER(wt.BOOL)],
                   pic.value, VK_RMENU, lparam_up, ctypes.byref(eaten))
        print(f"[10] OnKeyUp(VK_RMENU) hr=0x{hr & 0xFFFFFFFF:08X} eaten={bool(eaten.value)}",
              flush=True)
        time.sleep(3)

    print(f"[11] store text={store.text!r}")
    for entry in store.log[-10:]:
        print(f"[store] {entry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
