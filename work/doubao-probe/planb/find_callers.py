"""Find who calls a function, without a debugger.

Disassembles every function listed in the PE exception directory (.pdata) from its own start -
a linear sweep of .text desynchronises on data and misses call sites, this does not.

    python find_callers.py scratch/ImeService.exe 0x14077D780
"""
from __future__ import annotations

import sys

import capstone
import pefile


def main() -> int:
    path = sys.argv[1]
    target = int(sys.argv[2], 0)
    pe = pefile.PE(path)
    base = pe.OPTIONAL_HEADER.ImageBase
    blob = open(path, "rb").read()
    text = next(s for s in pe.sections if s.Name.startswith(b".text"))
    funcs = [(base + e.struct.BeginAddress, base + e.struct.EndAddress)
             for e in pe.DIRECTORY_ENTRY_EXCEPTION]
    funcs.sort()

    def off(va: int) -> int:
        return text.PointerToRawData + (va - base - text.VirtualAddress)

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    hits = []
    for start, end in funcs:
        if end - start > 0x20000:
            continue
        for insn in md.disasm(blob[off(start):off(start) + (end - start)], start):
            if insn.mnemonic == "call" and insn.op_str.startswith("0x"):
                if int(insn.op_str, 16) == target:
                    hits.append(insn.address)
    print(f"[callers of 0x{target:X}] {len(hits)}")
    for hit in hits:
        owner = next((f for f in funcs if f[0] <= hit < f[1]), None)
        print(f"  0x{hit:X}   in function 0x{owner[0]:X}" if owner else f"  0x{hit:X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
