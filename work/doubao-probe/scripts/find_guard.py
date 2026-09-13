"""Locate the 'sami start skipped because AppLog did is not ready' guard site.

Reads the PE image, finds the string in .rdata, locates RIP-relative references
in .text, and disassembles around them.
"""
import struct
import sys

import capstone
import pefile


def main(path: str, needle: str) -> None:
    pe = pefile.PE(path, fast_load=True)
    image_base = pe.OPTIONAL_HEADER.ImageBase
    buf = open(path, "rb").read()

    sections = []
    for s in pe.sections:
        name = s.Name.rstrip(b"\x00").decode("latin1")
        sections.append(
            (
                name,
                image_base + s.VirtualAddress,
                image_base + s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData),
                s.PointerToRawData,
                s.SizeOfRawData,
            )
        )

    def va_to_off(va: int):
        for _n, start, end, raw, size in sections:
            if start <= va < end:
                off = raw + (va - start)
                if off < len(buf):
                    return off
        return None

    text = next(s for s in sections if s[0] == ".text")
    rdata = [s for s in sections if s[0] in (".rdata", ".data")]

    # 1. find the string in any section
    raw = needle.encode("utf-8")
    hits = []
    for name, start, _end, raw_off, size in sections:
        blob = buf[raw_off:raw_off + size]
        pos = blob.find(raw)
        while pos != -1:
            hits.append((name, start + pos))
            pos = blob.find(raw, pos + 1)
    if not hits:
        print("string not found")
        return
    print("string occurrences:")
    for name, va in hits:
        print(f"  {name} VA=0x{va:X}")

    # 2. scan .text for RIP-relative LEA/MOV referencing those VAs
    #    (brute force over disp32 fields, robust against disassembly desync)
    t_name, t_start, t_end, t_raw, t_size = text
    code = buf[t_raw:t_raw + t_size]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True

    string_vas = {va for _n, va in hits}
    xrefs = []
    # direct rip-relative reference: look for disp32 fields whose target is the string
    for i in range(0, len(code) - 4):
        disp = struct.unpack_from("<i", code, i)[0]
        field_va = t_start + i
        # instruction decoding: try lengths 6..9 ending right after the disp32
        for insn_len in (6, 7, 8, 9):
            insn_start = field_va - (insn_len - 4)
            if insn_start < t_start:
                continue
            if insn_start + insn_len != field_va + 4:
                continue
            if insn_start + insn_len + disp in string_vas:
                xrefs.append((insn_start, "rip-disp32", insn_len))
                break
    print(f"xrefs to string: {len(xrefs)}")
    for addr, _mn, ln in xrefs:
        print(f"  site 0x{addr:X}  len={ln}")

    # 3. disassemble window before each xref site
    for addr, _mn, _ln in xrefs:
        print("=" * 70)
        print(f"context before 0x{addr:X}:")
        start = max(t_start, addr - 0x80)
        chunk = code[start - t_start:addr - t_start + 0x40]
        for insn in md.disasm(chunk, start):
            marker = "  <== site" if insn.address == addr else ""
            print(f"  0x{insn.address:X}: {insn.mnemonic:<8} {insn.op_str}{marker}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else
         "sami start skipped because AppLog did is not ready")
