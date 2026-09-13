"""Plan B prep: recover the argument shapes of rpc.dll exports from their call sites.

`tsf-oime-core.dll` is the vendor's own client, so every call it makes into `rpc.dll`
documents the real signature. This tool finds the import slot of a function in the
consumer's IAT, locates the `call qword ptr [rip+disp]` sites, and disassembles the
setup in front of each call.

Usage:
    python find_callsite.py <consumer.dll> <export_name> [--before 30]
"""
import argparse
import struct
import sys

import capstone
import pefile


class Image:
    def __init__(self, path: str):
        self.path = path
        self.pe = pefile.PE(path)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.blob = open(path, "rb").read()
        self.sections = []
        for s in self.pe.sections:
            self.sections.append(
                (
                    s.Name.rstrip(b"\x00").decode("latin1"),
                    self.base + s.VirtualAddress,
                    s.PointerToRawData,
                    s.SizeOfRawData,
                )
            )
        self.is64 = self.pe.FILE_HEADER.Machine == 0x8664

    def va_to_off(self, va: int):
        for _n, start, raw, size in self.sections:
            if start <= va < start + size:
                return raw + (va - start)
        return None

    def text(self):
        for name, start, raw, size in self.sections:
            if name == ".text":
                return start, self.blob[raw:raw + size]
        raise SystemExit("no .text section")

    def import_slots(self) -> dict[tuple[str, str], int]:
        """(dll_lower, function) -> VA of the IAT slot."""
        slots: dict[tuple[str, str], int] = {}
        for entry in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
            dll = entry.dll.decode("latin1").lower()
            for imp in entry.imports:
                name = (imp.name or b"").decode("latin1")
                if name:
                    slots[(dll, name)] = imp.address
        return slots


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("consumer")
    ap.add_argument("export")
    ap.add_argument("--before", type=int, default=30)
    args = ap.parse_args()

    img = Image(args.consumer)
    slots = img.import_slots()
    matches = [(k, v) for k, v in slots.items() if k[1] == args.export]
    if not matches:
        print(f"import not found: {args.export}")
        return 1
    (dll, name), slot_va = matches[0]
    print(f"{args.export} imported from {dll}, IAT slot VA=0x{slot_va:X}")

    text_va, code = img.text()
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True

    # scan for `FF 15 disp32` (call qword ptr [rip+disp32]) whose target is the slot
    sites = []
    for i in range(len(code) - 6):
        if code[i] == 0xFF and code[i + 1] == 0x15:
            disp = struct.unpack_from("<i", code, i + 2)[0]
            site_va = text_va + i
            if site_va + 6 + disp == slot_va:
                sites.append(site_va)
    print(f"call sites: {len(sites)}")

    for site in sites[:3]:
        print("=" * 72)
        print(f"--- setup before call at 0x{site:X} ---")
        start = site - 0x8 * args.before
        off = img.va_to_off(start)
        chunk = img.blob[off:off + 0x8 * args.before + 0x20]
        for insn in md.disasm(chunk, start):
            mark = "   <== call" if insn.address == site else ""
            print(f"  0x{insn.address:X}: {insn.mnemonic:<9} {insn.op_str}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
