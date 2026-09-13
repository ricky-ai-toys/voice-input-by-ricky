"""Plan B: enumerate rpc.dll's wire op codes and describe each handler.

rpc.dll dispatches incoming "OMPE" frames with a jump table indexed by (op - 1):

    movzx eax, dx                 ; op
    dec   eax
    cmp   eax, 0x1b               ; ops 0x01..0x1C
    ja    default
    lea   rdx, [rip - 0x23864]    ; image base
    mov   ecx, [rdx + rax*4 + 0x25688]
    add   rcx, rdx
    jmp   rcx

This tool walks that table, then disassembles each handler and prints the strings it
references (log messages, field names) so the ops can be named without a debugger.

    python dump_ops.py scratch/rpc.dll            # all ops
    python dump_ops.py scratch/rpc.dll 0x0C       # one op, full listing
"""
from __future__ import annotations

import struct
import sys

import capstone
import pefile

TABLE_VA = 0x180025688
TABLE_LEN = 0x1C


class Pe:
    def __init__(self, path: str):
        self.path = path
        self.pe = pefile.PE(path)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.blob = open(path, "rb").read()
        self.sections = []
        for s in self.pe.sections:
            name = s.Name.rstrip(b"\x00").decode("latin1")
            self.sections.append((name, self.base + s.VirtualAddress,
                                  self.base + s.VirtualAddress + max(s.SizeOfRawData, s.Misc_VirtualSize),
                                  s.PointerToRawData, s.SizeOfRawData))

    def off(self, va: int):
        for _n, lo, _hi, off, size in self.sections:
            if lo <= va < lo + size:
                return off + (va - lo)
        return None

    def section_name(self, va: int) -> str:
        for name, lo, hi, _off, _size in self.sections:
            if lo <= va < hi:
                return name
        return "?"

    def string_at(self, va: int, limit: int = 200) -> str | None:
        off = self.off(va)
        if off is None:
            return None
        raw = self.blob[off:off + limit]
        end = raw.find(b"\x00")
        if end < 1:
            return None
        text = raw[:end].decode("latin1")
        if sum(c.isprintable() for c in text) < len(text) * 0.9:
            return None
        return text

    def qword(self, va: int):
        off = self.off(va)
        if off is None:
            return None
        return struct.unpack_from("<Q", self.blob, off)[0]


def op_table(pe: Pe) -> dict[int, int]:
    out = {}
    for i in range(TABLE_LEN):
        off = pe.off(TABLE_VA + i * 4)
        out[i + 1] = pe.base + struct.unpack_from("<i", pe.blob, off)[0]
    return out


def describe(pe: Pe, md: capstone.Cs, start: int, end: int, verbose: bool) -> list[str]:
    data = pe.blob[pe.off(start):pe.off(start) + (end - start)]
    strings: list[str] = []
    calls: list[int] = []
    lines: list[str] = []
    for insn in md.disasm(data, start):
        note = ""
        target = None
        if insn.mnemonic == "lea" and "rip" in insn.op_str:
            disp = None
            for part in insn.op_str.split(","):
                if "rip" in part:
                    disp = part
            if disp:
                inner = disp[disp.find("[") + 1:disp.find("]")]
                try:
                    delta = int(inner.split("rip")[1].split("+")[-1].split("-")[-1], 16)
                except Exception:
                    delta = None
                if delta is not None:
                    sign = -1 if "-" in inner.split("rip")[1] else 1
                    target = insn.address + insn.size + sign * delta
        elif insn.mnemonic == "mov" and "rip" in insn.op_str and insn.size == 7:
            inner = insn.op_str[insn.op_str.find("[") + 1:insn.op_str.find("]")]
            try:
                delta = int(inner.split("rip")[1].split("+")[-1].split("-")[-1], 16)
            except Exception:
                delta = None
            if delta is not None:
                sign = -1 if "-" in inner.split("rip")[1] else 1
                target = insn.address + insn.size + sign * delta
        if target is not None:
            seg = pe.section_name(target)
            if seg in (".rdata", ".data"):
                s = pe.string_at(target)
                if s and len(s) >= 4:
                    note = f"   ; str {s!r}"
                    strings.append(s)
                else:
                    q = pe.qword(target)
                    if q:
                        s2 = pe.string_at(q)
                        if s2 and len(s2) >= 4:
                            note = f"   ; ptr->{s2!r}"
                            strings.append(s2)
        if insn.mnemonic == "call":
            op = insn.op_str
            if op.startswith("0x"):
                calls.append(int(op, 16))
            note = (note + f"   ; call {op}").strip()
        lines.append(f"  0x{insn.address:X}: {insn.mnemonic:<8} {insn.op_str}{note}"
                     if verbose else "")
    if verbose:
        return lines
    summary = []
    seen = []
    for s in strings:
        if s not in seen:
            seen.append(s)
    if seen:
        summary.append("   strings: " + " | ".join(repr(s) for s in seen[:12]))
    uniq_calls = []
    for c in calls:
        if c not in uniq_calls:
            uniq_calls.append(c)
    if uniq_calls:
        summary.append("   calls:   " + " ".join(f"0x{c:X}" for c in uniq_calls[:16]))
    return summary


def main() -> int:
    path = sys.argv[1]
    pe = Pe(path)
    table = op_table(pe)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    order = sorted(table, key=lambda o: table[o])
    next_of = {}
    for i, op in enumerate(order):
        next_of[op] = table[order[i + 1]] if i + 1 < len(order) else table[order[-1]] + 0x60

    want = None
    if len(sys.argv) > 2:
        want = int(sys.argv[2], 0)
    for op in order:
        if want is not None and op != want:
            continue
        start = table[op]
        end = next_of[op]
        print(f"=== op 0x{op:02X}  handler 0x{start:X} (+{end - start} bytes) ===")
        for line in describe(pe, md, start, end, verbose=(want is not None)):
            if line:
                print(line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
