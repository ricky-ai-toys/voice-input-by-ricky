"""Plan B: drive the engine's private pipe directly (no vendor client DLL involved).

Wire format recovered from rpc.dll (see dump_ops.py / dump_proto.py):

    request : "OMPE" + u16 version(1) + u16 op + u32 body_len + body          (12 + N)
    response: "OMPE" + u16 version(1) + u16 op + u32 status + u32 body_len + body

Ops (server.proto, package oime):
    0x01 SendHeart        0x03 DeActivate     0x04 KeyDown        0x05 KeyUp
    0x06 FocusIn*         0x07 FocusOut       0x09 GetCompText    0x0A GetCommitText
    0x0B GetCandidateList 0x0C SetUIElementShowState                0x0D ReloadConfig
    0x0E SimpleMessage    0x0F GetInputState  0x10 SetInputState   0x11 ConfigChanged
    0x12 LogEvent         0x13 ImeChanged     0x17 PeekVoiceCommit 0x18 AckVoiceCommit
    0x19 GetInlineCommitText
(*op numbering follows rpc.dll's dispatch table; names from the embedded descriptor and
 from the handler strings such as "FocusOut"/"KeyDown"/"PeekVoiceCommit".)

    python pipe_client.py <runtime_dir> [--speak "text"] [--keep-server]

Runs against the **private** copy only: the engine in <runtime_dir> has its pipe renamed to
`\\\\.\\pipe\\ObricIme\\oime-serveR`, so the installed Doubao IME is never touched.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

PIPE = "\\\\.\\pipe\\ObricIme\\oime-serve1"
LOG_NAME = "pipe_client_server.log"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                            wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                          ctypes.c_void_p]
k32.WriteFile.restype = wt.BOOL
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                         ctypes.c_void_p]
k32.ReadFile.restype = wt.BOOL
k32.PeekNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                              ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
                              ctypes.POINTER(wt.DWORD)]
k32.PeekNamedPipe.restype = wt.BOOL


# --------------------------------------------------------------------------- #
# protobuf helpers (only what the voice calls need)
# --------------------------------------------------------------------------- #
def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def pb_int(field: int, value: int) -> bytes:
    return varint(field << 3) + varint(value)


def pb_str(field: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return varint((field << 3) | 2) + varint(len(raw)) + raw


# --------------------------------------------------------------------------- #
# pipe
# --------------------------------------------------------------------------- #
class Pipe:
    def __init__(self, name: str = PIPE, timeout: float = 30.0):
        self.name = name
        self.handle = None
        deadline = time.time() + timeout
        last = 0
        while time.time() < deadline:
            handle = k32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None,
                                     OPEN_EXISTING, 0, None)
            if handle not in (None, 0, INVALID_HANDLE_VALUE):
                self.handle = handle
                return
            last = ctypes.get_last_error()
            # 2 = pipe not created yet, 231 = all instances busy
            if last not in (2, 231):
                break
            time.sleep(0.05)
        raise OSError(f"CreateFileW({name!r}) failed err={last}")

    def close(self) -> None:
        if self.handle:
            k32.CloseHandle(self.handle)
            self.handle = None

    def write(self, data: bytes) -> None:
        written = wt.DWORD(0)
        buf = ctypes.create_string_buffer(data, len(data))
        if not k32.WriteFile(self.handle, buf, len(data), ctypes.byref(written), None):
            raise OSError(f"WriteFile failed err={ctypes.get_last_error()}")

    def _available(self) -> int:
        avail = wt.DWORD(0)
        if not k32.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(avail), None):
            raise OSError(f"PeekNamedPipe failed err={ctypes.get_last_error()}")
        return avail.value

    def read_exact(self, size: int, timeout: float = 5.0) -> bytes | None:
        deadline = time.time() + timeout
        out = b""
        while len(out) < size:
            if time.time() > deadline:
                return None
            if self._available() < size - len(out):
                time.sleep(0.01)
                continue
            chunk = ctypes.create_string_buffer(size - len(out))
            got = wt.DWORD(0)
            if not k32.ReadFile(self.handle, chunk, size - len(out), ctypes.byref(got), None):
                raise OSError(f"ReadFile failed err={ctypes.get_last_error()}")
            out += chunk.raw[:got.value]
        return out

    def call(self, op: int, body: bytes = b"", timeout: float = 8.0):
        """Send one request; return (op, body, status) for the response carrying our op."""
        header = b"OMPE" + (1).to_bytes(2, "little") + op.to_bytes(2, "little") + \
                 len(body).to_bytes(4, "little")
        self.write(header + body)
        deadline = time.time() + timeout
        while time.time() < deadline:
            head = self.read_exact(16, timeout=max(0.1, deadline - time.time()))
            if head is None:
                return None
            if head[:4] != b"OMPE":
                print(f"    [warn] misaligned frame {head!r}")
                continue
            r_op = int.from_bytes(head[6:8], "little")
            r_status = int.from_bytes(head[8:12], "little")
            r_len = int.from_bytes(head[12:16], "little")
            payload = self.read_exact(r_len, timeout=3.0) if r_len else b""
            if payload is None:
                print(f"    [warn] body of op 0x{r_op:02X} did not arrive ({r_len} bytes)")
                return None
            if r_op == op:
                return (r_op, payload, r_status)
            print(f"    [note] unsolicited frame op=0x{r_op:02X} status={r_status} len={r_len} "
                  f"body={payload[:48].hex()}")
        return None


def pb_dump(body: bytes) -> str:
    """Best effort: show fields as varints/strings."""
    out = []
    pos = 0
    while pos < len(body):
        try:
            key = 0
            shift = 0
            while True:
                byte = body[pos]
                pos += 1
                key |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
                shift += 7
            field, wire = key >> 3, key & 7
            if wire == 0:
                value = 0
                shift = 0
                while True:
                    byte = body[pos]
                    pos += 1
                    value |= (byte & 0x7F) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                out.append(f"f{field}={value}")
            elif wire == 2:
                size = 0
                shift = 0
                while True:
                    byte = body[pos]
                    pos += 1
                    size |= (byte & 0x7F) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                raw = body[pos:pos + size]
                pos += size
                try:
                    text = raw.decode("utf-8")
                    if text.isprintable():
                        out.append(f"f{field}={text!r}")
                    else:
                        out.append(f"f{field}=<{size}B>")
                except UnicodeDecodeError:
                    out.append(f"f{field}=<{size}B>")
            else:
                out.append(f"f{field}=wire{wire}")
                break
        except IndexError:
            break
    return " ".join(out)


# --------------------------------------------------------------------------- #
# server control
# --------------------------------------------------------------------------- #
def start_server(runtime: str) -> subprocess.Popen:
    exe = os.path.join(runtime, "ImeService.exe")
    log_path = os.path.join(runtime, LOG_NAME)
    log = open(log_path, "wb")
    proc = subprocess.Popen([exe], cwd=runtime, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=0x00000008)  # DETACHED_PROCESS
    return proc


def log_tail(runtime: str, count: int = 25) -> None:
    path = os.path.join(runtime, LOG_NAME)
    if not os.path.exists(path):
        return
    with open(path, "rb") as fh:
        data = fh.read().decode("utf-8", "replace").splitlines()
    print(f"--- last {count} server log lines ---")
    for line in data[-count:]:
        if line.strip():
            print("   " + line.strip())


KEYWORDS = ("voice", "Voice", "key", "Key", "reject", "Reject", "focus", "Focus",
            "session", "Session", "mic", "Mic", "asr", "ASR", "record", "Record")


def log_grep(runtime: str, count: int = 40) -> None:
    """Show the recent server log lines that could explain a voice session."""
    path = os.path.join(runtime, LOG_NAME)
    if not os.path.exists(path):
        return
    with open(path, "rb") as fh:
        data = fh.read().decode("utf-8", "replace").splitlines()
    hits = [ln.strip() for ln in data if any(k in ln for k in KEYWORDS)]
    print(f"--- {len(hits)} matching server log lines (last {count}) ---")
    for line in hits[-count:]:
        print("   " + line)


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    keep = "--keep-server" in sys.argv
    proc = None
    try:
        pipe = Pipe(timeout=1.5)
    except OSError:
        pipe = None
    if pipe is None:
        print("[info] starting private engine copy ...")
        proc = start_server(runtime)
        try:
            pipe = Pipe(timeout=35.0)
        except OSError as exc:
            print(f"[fail] private pipe never became connectable: {exc}")
            log_tail(runtime, 20)
            return 1
    print(f"[ok] private pipe is up")

    host = os.path.basename(sys.executable or "python.exe")

    if "--probe" in sys.argv:
        print("--- op sweep (empty bodies) ---")
        for op in range(0x01, 0x1D):
            res = pipe.call(op, b"", timeout=3.0)
            if res is None:
                print(f"  0x{op:02X}: no response")
            else:
                extra = pb_dump(res[1]) if res[1] else ""
                print(f"  0x{op:02X}: status={res[2]} len={len(res[1])} {extra}")
        pipe.close()
        log_grep(runtime)
        if proc and not keep:
            proc.terminate()
        return 0

    if "--send" in sys.argv:
        """Ad-hoc frames: --send 16:0801 --send 17: --wait 2.0"""
        sends = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--send"]
        wait = 1.0
        if "--wait" in sys.argv:
            wait = float(sys.argv[sys.argv.index("--wait") + 1])
        for spec in sends:
            op_txt, _, body_txt = spec.partition(":")
            op = int(op_txt, 0)
            body = bytes.fromhex(body_txt) if body_txt else b""
            res = pipe.call(op, body, timeout=5.0)
            extra = ""
            if res is not None and res[1]:
                extra = " " + pb_dump(res[1])
            print(f"[send 0x{op:02X}] body={body.hex()} -> "
                  f"{res if res is None else (res[2], len(res[1]))}{extra}")
            time.sleep(wait)
        pipe.close()
        log_grep(runtime, 25)
        if proc and not keep:
            proc.terminate()
        return 0

    if "--replay-context" in sys.argv:
        # exact body the vendor's TSF core sent for op 6 while focusing a real app
        # (captured in evidence/pipe_traffic_profile.txt): [u32][u32 1][u32 len]["python.exe"]
        ctx = bytes.fromhex("08860000010000000a000000707974686f6e2e657865")
        res = pipe.call(0x06, ctx)
        print(f"[FocusIn(host ctx)] {res if res is None else (res[2], pb_dump(res[1]))}")
        for key in (0xA5, 0x20, 0x00):
            res = pipe.call(0x04, pb_int(1, key) + pb_str(3, host))
            print(f"[KeyDown 0x{key:02X}] {res if res is None else res[2]}")
            time.sleep(3.0)
            res = pipe.call(0x17, b"")
            print(f"   [peek] {res if res is None else (res[2], pb_dump(res[1]))}")
            res = pipe.call(0x05, pb_int(1, key) + pb_str(3, host))
            print(f"[KeyUp   0x{key:02X}] {res if res is None else res[2]}")
        pipe.close()
        log_grep(runtime)
        if proc and not keep:
            proc.terminate()
        return 0

    steps = [
        (0x01, b"", "SendHeart"),
        (0x06, b"", "FocusIn (empty)"),
        (0x06, pb_str(1, host), "FocusIn (host name)"),
        (0x0F, pb_int(1, 0), "GetInputState"),
        (0x17, b"", "PeekVoiceCommit (before)"),
    ]
    for op, body, label in steps:
        res = pipe.call(op, body)
        if res is None:
            print(f"[{label}] no response")
        else:
            print(f"[{label}] op=0x{res[0]:02X} status={res[2]} len={len(res[1])} "
                  f"{pb_dump(res[1])}")

    key_code = 0xA5  # VK_RMENU - the Doubao voice hotkey
    body = pb_int(1, key_code) + pb_str(3, host)
    print(f"[KeyDown] sending op 0x04 keycode=0x{key_code:X} host={host}")
    res = pipe.call(0x04, body)
    print(f"[KeyDown] -> {res if res is None else (res[2], pb_dump(res[1]))}")

    for i in range(10):
        time.sleep(0.5)
        res = pipe.call(0x17, b"")
        text = None
        if res is not None:
            text = res[1]
        print(f"[PeekVoiceCommit {i}] status={res[2] if res else '-'} "
              f"len={len(text) if text else 0} "
              f"{pb_dump(text) if text else ''}")

    print("[KeyUp] sending op 0x05")
    res = pipe.call(0x05, body)
    print(f"[KeyUp] -> {res if res is None else (res[2], pb_dump(res[1]))}")

    pipe.close()
    log_grep(runtime)
    if proc and not keep:
        proc.terminate()
        print("[info] private engine stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
