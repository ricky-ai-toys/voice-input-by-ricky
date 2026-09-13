"""Plan B: read the server's op table from ImeService.exe.

`ImeService.exe` imports only four rpc.dll functions; the interesting one is
`CreateRpcServer`, whose result is a C++ object whose (non-exported) methods register one
handler per op code. Disassembling what follows the CreateRpcServer call therefore reveals
the op numbers - that is how we learn which op starts a voice session.

    python dump_server_registrations.py <runtime_dir>
"""
import ctypes
import os
import struct
import sys

import capstone
import pefile


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    exe = os.path.join(runtime, "ImeService.exe")
    pe = pefile.PE(exe)
    base = pe.OPTIONAL_HEADER.ImageBase
    blob = open(exe, "rb").read()
    secs = [(s.Name.rstrip(b"\x00").decode("latin1"), base + s.VirtualAddress,
             s.PointerToRawData, s.SizeOfRawData) for s in pe.sections]

    slot = None
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        if entry.dll.decode("latin1").lower() != "rpc.dll":
            continue
        for imp in entry.imports:
            name = (imp.name or b"").decode("latin1")
            if "CreateRpcServer" in name:
                slot = imp.address
                print(f"[info] CreateRpcServer IAT slot 0x{slot:X} ({name})")
    if not slot:
        print("[fail] CreateRpcServer not imported")
        return 1

    text = next(s for s in secs if s[0] == ".text")
    text_va, text_off, text_size = text[1], text[2], text[3]
    code = blob[text_off:text_off + text_size]

    thunks = []
    for i in range(len(code) - 6):
        if code[i] == 0xFF and code[i + 1] == 0x25:
            disp = struct.unpack_from("<i", code, i + 2)[0]
            if text_va + i + 6 + disp == slot:
                thunks.append(text_va + i)
    print(f"[info] delay-load thunks: {[hex(t) for t in thunks]}")

    callers = []
    for thunk in thunks:
        for i in range(len(code) - 5):
            if code[i] == 0xE8:
                rel = struct.unpack_from("<i", code, i + 1)[0]
                if text_va + i + 5 + rel == thunk:
                    callers.append(text_va + i)
    print(f"[info] callers: {[hex(c) for c in callers]}")

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    for call in callers[:2]:
        print("=" * 72)
        print(f"--- code after CreateRpcServer call at 0x{call:X} ---")
        off = call - text_va
        chunk = code[off:off + 1200]
        for insn in md.disasm(chunk, call):
            mark = ""
            if insn.mnemonic == "mov" and "0x" in insn.op_str and "rip" not in insn.op_str:
                mark = "   <-- constant?"
            print(f"  0x{insn.address:X}: {insn.mnemonic:<8} {insn.op_str}{mark}")
            if insn.address > call + 700:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
