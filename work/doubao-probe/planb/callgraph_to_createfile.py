"""Plan B: walk the call graph of rpc.dll to find which code path opens the pipe.

1. locate the IAT slot of kernel32!CreateFileW inside rpc.dll
2. find every `call qword ptr [rip+disp]` that targets that slot
3. linearly disassemble rpc.dll, build caller -> callee edges for `call rel32`
4. BFS from the CreateFile* sites upwards and report which of them are reachable from the
   client vtable methods (the addresses we captured at runtime)

    python callgraph_to_createfile.py <runtime_dir>
"""
import ctypes
import os
import struct
import sys

import capstone
import pefile


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    dll_path = os.path.join(runtime, "rpc.dll")
    pe = pefile.PE(dll_path)
    base = pe.OPTIONAL_HEADER.ImageBase
    blob = open(dll_path, "rb").read()

    secs = [(s.Name.rstrip(b"\x00").decode("latin1"), base + s.VirtualAddress,
             s.PointerToRawData, s.SizeOfRawData) for s in pe.sections]

    def va_to_off(va):
        for _n, start, raw, size in secs:
            if start <= va < start + size:
                return raw + (va - start)
        return None

    # 1. IAT slot of CreateFileW
    slot = None
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        if entry.dll.decode("latin1").lower() != "kernel32.dll":
            continue
        for imp in entry.imports:
            if imp.name and imp.name.decode("latin1") in ("CreateFileW", "CreateFileA"):
                print(f"[info] {imp.name.decode()} IAT slot 0x{imp.address:X}")
                slot = imp.address
    if not slot:
        print("[fail] CreateFileW/A not imported by rpc.dll")
        return 1

    text = next(s for s in secs if s[0] == ".text")
    text_va, text_off, text_size = text[1], text[2], text[3]
    code = blob[text_off:text_off + text_size]

    # 2. call sites that go through the slot
    create_sites = []
    for i in range(len(code) - 6):
        if code[i] == 0xFF and code[i + 1] == 0x15:
            disp = struct.unpack_from("<i", code, i + 2)[0]
            if text_va + i + 6 + disp == slot:
                create_sites.append(text_va + i)
    print(f"[info] call sites via IAT: {[hex(x) for x in create_sites]}")
    if not create_sites:
        return 1

    # 3. caller -> callees (rel32) map
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    edges: dict[int, set[int]] = {}
    for insn in md.disasm(code, text_va):
        if insn.mnemonic == "call" and not insn.op_str.startswith(("qword", "rax", "rcx")):
            try:
                target = int(insn.op_str, 16)
            except ValueError:
                continue
            if text_va <= target < text_va + text_size:
                edges.setdefault(insn.address, set()).add(target)

    # 4. reverse reachability from the CreateFile sites
    reverse: dict[int, set[int]] = {}
    for caller, callees in edges.items():
        for callee in callees:
            reverse.setdefault(callee, set()).add(caller)

    reached = set(create_sites)
    frontier = list(create_sites)
    while frontier:
        current = frontier.pop()
        for caller in reverse.get(current, ()):
            if caller not in reached:
                reached.add(caller)
                frontier.append(caller)
    print(f"[info] {len(reached)} code addresses can reach a CreateFile* call")

    # runtime vtable addresses (from the last capture) for cross-checking
    dll = ctypes.CDLL(dll_path)
    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    obj = create(b"\\\\.\\pipe\\ObricIme\\oime-serveR")
    k32 = ctypes.windll.kernel32
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    rt_base = k32.GetModuleHandleW("rpc.dll")
    vtable = ctypes.c_void_p.from_address(obj).value
    for i in range(12):
        fn = ctypes.c_void_p.from_address(vtable + i * 8).value
        if not fn:
            break
        rva = base + (fn - rt_base)
        reachable = rva in reached
        print(f"[vtable {i:2d}] rva=0x{rva - base:X} can-reach-CreateFile={reachable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
