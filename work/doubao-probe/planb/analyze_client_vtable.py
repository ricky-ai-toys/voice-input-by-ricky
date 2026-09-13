"""Plan B: label the vtable methods of the CreateRpcClient() object.

For every vtable entry: resolve it to an rpc.dll RVA, disassemble the first instructions and
report what it calls (CreateFileW = connect, pipe read/write = transport) plus any log
strings it references, so we can identify connect / send / peek / ack.

    python analyze_client_vtable.py <runtime_dir> [pipe_name]
"""
import ctypes
import os
import re
import struct
import sys

import capstone
import pefile


def module_base(name: str) -> int:
    k32 = ctypes.windll.kernel32
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    handle = k32.GetModuleHandleW(name)
    if not handle:
        raise SystemExit(f"{name} not loaded")
    return handle


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    pipe = sys.argv[2] if len(sys.argv) > 2 else "\\\\.\\pipe\\ObricIme\\oime-serveR"
    os.add_dll_directory(runtime)
    dll_path = os.path.join(runtime, "rpc.dll")
    dll = ctypes.CDLL(dll_path)

    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    obj = create(pipe.encode("utf-8"))
    if not obj:
        print("[fail] CreateRpcClient returned NULL")
        return 1

    base = module_base("rpc.dll")
    vtable = ctypes.c_void_p.from_address(obj).value
    print(f"[info] object=0x{obj:X} vtable=0x{vtable:X} rpc.dll base=0x{base:X}")

    blob = open(dll_path, "rb").read()
    pe = pefile.PE(dll_path, fast_load=True)
    secs = [(s.VirtualAddress, s.PointerToRawData, s.SizeOfRawData) for s in pe.sections]

    def rva_to_off(va: int):
        rva = va - base
        for start, raw, size in secs:
            if start <= rva < start + size:
                return raw + (rva - start)
        return None

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    strings = [m.group(0).decode("latin1") for m in re.finditer(rb"[\x20-\x7e]{6,}", blob)]

    for i in range(12):
        fn = ctypes.c_void_p.from_address(vtable + i * 8).value
        if not fn:
            break
        rva = fn - base
        off = rva_to_off(fn)
        print("=" * 70)
        print(f"[vtable {i:2d}] 0x{fn:X}  rpc.dll+0x{rva:X}")
        if off is None:
            print("   (outside image)")
            continue
        calls = []
        for insn in md.disasm(blob[off:off + 400], fn):
            if insn.mnemonic == "call" or insn.mnemonic == "jmp":
                calls.append(f"{insn.mnemonic} {insn.op_str}")
            if len(calls) >= 8:
                break
        for c in calls:
            print(f"   {c}")
        # nearby strings hint at the method's purpose
        hint = [s for s in strings if re.search(r"PipeCall|connect|Connect|Voice|Key|Focus|read|write", s)][:0]
        del hint
    return 0


if __name__ == "__main__":
    sys.exit(main())
