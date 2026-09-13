"""Plan B: which vtable method of the rpc client object is Connect(pipe_name)?

Resolves every `call qword ptr [rip+disp32]` inside each vtable method at runtime and
compares the target with known kernel32 transport APIs (CreateFileW/CreateFileA/ReadFile/
WriteFile/WaitNamedPipeW/...). The method that calls CreateFile* is the one that opens the
named pipe - our Connect.

    python find_connect_method.py <runtime_dir> [pipe_name]
"""
import ctypes
import os
import sys

import capstone

K32 = ctypes.windll.kernel32
WATCHED = [
    "CreateFileW", "CreateFileA", "ReadFile", "WriteFile", "DeviceIoControl",
    "WaitNamedPipeW", "WaitNamedPipeA", "CreateEventW", "WaitForSingleObject",
    "SetEvent", "GetLastError", "Sleep", "PeekNamedPipe", "FlushFileBuffers",
]


def addr_of(name: str) -> int | None:
    try:
        return ctypes.cast(getattr(K32, name), ctypes.c_void_p).value
    except AttributeError:
        return None


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    pipe = sys.argv[2] if len(sys.argv) > 2 else "\\\\.\\pipe\\ObricIme\\oime-serveR"
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))

    watch = {addr_of(n): n for n in WATCHED if addr_of(n)}
    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    obj = create(pipe.encode("utf-8"))
    vtable = ctypes.c_void_p.from_address(obj).value
    print(f"[info] client object 0x{obj:X}, vtable 0x{vtable:X}")

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = False

    for i in range(12):
        fn = ctypes.c_void_p.from_address(vtable + i * 8).value
        if not fn:
            break
        code = ctypes.string_at(fn, 512)
        hits = []
        for insn in md.disasm(code, fn):
            if insn.mnemonic == "call" and insn.op_str.startswith("qword ptr [rip"):
                # rip-relative IAT slot -> read the pointer stored there
                try:
                    disp = insn.disp
                    slot = insn.address + insn.size + disp
                    target = ctypes.c_void_p.from_address(slot).value
                except Exception:
                    continue
                name = watch.get(target)
                hits.append(name or f"0x{target:X}")
            if len(hits) >= 10:
                break
        interesting = [h for h in hits if not h.startswith("0x")]
        flag = "  <== transport" if any(h.startswith("CreateFile") or h.startswith("ReadFile")
                                       or h.startswith("WriteFile") or "Pipe" in h
                                       or h.startswith("DeviceIo") for h in interesting) else ""
        print(f"[vtable {i:2d}] rpc-internal calls: {', '.join(hits) or '(none)'}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
