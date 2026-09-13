"""Find code sites referencing literal strings in a PE image (brute-force RIP disp32)."""
import struct
import sys

import capstone
import pefile


def main(path: str, needles: list[str]) -> None:
    pe = pefile.PE(path, fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    buf = open(path, "rb").read()

    secs = []
    for s in pe.sections:
        secs.append(
            (
                s.Name.rstrip(b"\x00").decode("latin1"),
                base + s.VirtualAddress,
                s.PointerToRawData,
                s.SizeOfRawData,
            )
        )

    def va2off(va):
        for _n, start, raw, size in secs:
            if start <= va < start + size:
                return raw + (va - start)
        return None

    text = next(s for s in secs if s[0] == ".text")
    t_start, t_raw, t_size = text[1], text[2], text[3]
    code = buf[t_raw:t_raw + t_size]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)

    for needle in needles:
        raw = needle.encode("utf-8")
        hits = []
        for name, start, raw_off, size in secs:
            blob = buf[raw_off:raw_off + size]
            pos = blob.find(raw)
            while pos != -1:
                hits.append((name, start + pos))
                pos = blob.find(raw, pos + 1)
        print(f"\n### needle {needle!r}: {len(hits)} occurrence(s)")
        for name, va in hits:
            print(f"  {name} VA=0x{va:X}")
        if not hits:
            continue
        targets = {va for _n, va in hits}
        for i in range(0, len(code) - 4):
            disp = struct.unpack_from("<i", code, i)[0]
            field_va = t_start + i
            for insn_len in (6, 7, 8, 9, 10):
                insn_start = field_va - (insn_len - 4)
                if insn_start < t_start:
                    continue
                if insn_start + insn_len + disp in targets:
                    print(f"  xref site 0x{insn_start:X}")
                    start = max(t_start, insn_start - 0x60)
                    chunk = code[start - t_start:insn_start - t_start + 0x60]
                    for insn in md.disasm(chunk, start):
                        mark = "  <== here" if insn.address == insn_start else ""
                        print(f"    0x{insn.address:X}: {insn.mnemonic:<9} {insn.op_str}{mark}")
                    break


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
