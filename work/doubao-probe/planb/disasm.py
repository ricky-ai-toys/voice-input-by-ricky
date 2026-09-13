"""Plan B helper: disassemble a function (or range) of a PE without a GUI disassembler.

    python disasm.py <pe-file> <va-or-file-offset> [count] [--func] [--find-bytes HEX]

Examples
    python disasm.py scratch/rpc.dll 0x180026880 --func
    python disasm.py scratch/rpc.dll --find-bytes 4f4d5045

`--func` uses the PE exception table (.pdata) to clip the dump to the enclosing function,
which is what we need to read the server's frame parser and its op dispatch.
"""
from __future__ import annotations

import sys

import capstone
import pefile


def load(path: str):
    pe = pefile.PE(path)
    base = pe.OPTIONAL_HEADER.ImageBase
    blob = open(path, "rb").read()
    sections = []
    for s in pe.sections:
        sections.append((s.Name.rstrip(b"\x00").decode("latin1"), base + s.VirtualAddress,
                         base + s.VirtualAddress + s.SizeOfRawData,
                         s.PointerToRawData, s.SizeOfRawData))
    funcs = []
    try:
        for e in pe.DIRECTORY_ENTRY_EXCEPTION:
            funcs.append((base + e.struct.BeginAddress, base + e.struct.EndAddress))
    except AttributeError:
        pass
    return pe, base, blob, sections, funcs


def to_va(sections, value: int) -> int:
    for _name, va_lo, _va_hi, off, size in sections:
        if off <= value < off + size:
            return va_lo + (value - off)
    return value


def read_code(sections, blob, va_lo: int, va_hi: int) -> bytes:
    for _name, s_lo, s_hi, off, _size in sections:
        if s_lo <= va_lo < s_hi:
            start = off + (va_lo - s_lo)
            end = off + (min(va_hi, s_hi) - s_lo)
            return blob[start:end]
    raise SystemExit(f"0x{va_lo:X} is not inside a section")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    path = args.pop(0)
    pe, base, blob, sections, funcs = load(path)

    if args and args[0] == "--find-bytes":
        needle = bytes.fromhex(args[1])
        for _name, s_lo, s_hi, off, size in sections:
            chunk = blob[off:off + size]
            pos = 0
            while True:
                idx = chunk.find(needle, pos)
                if idx < 0:
                    break
                print(f"0x{s_lo + idx:X}")
                pos = idx + 1
        return 0

    target = to_va(sections, int(args.pop(0), 0))
    count = int(args.pop(0), 0) if args and not args[0].startswith("-") else 400
    clip = "--func" in args
    lo, hi = target, target + 0x1000
    if clip:
        for s, e in funcs:
            if s <= target < e:
                lo, hi = s, e
                print(f"[func] 0x{s:X} - 0x{e:X}  ({e - s} bytes)")
                break
        else:
            print("[func] not found in .pdata; dumping raw")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    data = read_code(sections, blob, lo, hi)

    def annotate(insn) -> str:
        """Resolve rip-relative operands so string literals show up inline."""
        if "rip" not in insn.op_str:
            return ""
        start = insn.op_str.find("[")
        end = insn.op_str.find("]", start)
        inner = insn.op_str[start + 1:end]
        tail = inner.split("rip")[1]
        sign = -1 if "-" in tail else 1
        digits = tail.split("+")[-1].split("-")[-1]
        try:
            delta = int(digits, 16)
        except ValueError:
            return ""
        target = insn.address + insn.size + sign * delta
        for name, s_lo, s_hi, off, _size in sections:
            if s_lo <= target < s_hi and name in (".rdata", ".data"):
                raw = blob[off + (target - s_lo):][:120]
                end0 = raw.find(b"\x00")
                if end0 >= 2:
                    text = raw[:end0].decode("utf-8", "replace")
                    if sum(c.isprintable() for c in text) >= len(text) * 0.9:
                        return f"   ; {text!r}"
                return f"   ; ->0x{target:X}"
        return f"   ; ->0x{target:X}"

    shown = 0
    for insn in md.disasm(data, lo):
        print(f"  0x{insn.address:X}: {insn.bytes.hex():<20} {insn.mnemonic:<8} "
              f"{insn.op_str}{annotate(insn)}")
        shown += 1
        if not clip and shown >= count:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
