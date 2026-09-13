"""Plan B: let injected key events reach the engine's voice hotkey.

`VoiceKeyHookProc` (ImeService.exe, server_main.cpp) deliberately drops synthetic input:

    0x140742743: test byte ptr [r8 + 8], 0x10   ; KBDLLHOOKSTRUCT.flags & LLKHF_INJECTED
    0x140742748: jne  0x14074275b               ; injected -> skip voice handling

That check is what stops any programmatic hotkey from starting a voice session. This tool
replaces those 7 bytes with NOPs **in our private copy only**, so a client can trigger the
engine's own voice pipeline (mic capture + cloud ASR) with SendInput/keybd_event.

    python patch_voicehook.py <runtime_dir>
"""
from __future__ import annotations

import os
import sys

import pefile

# 1) injected-key filter: test byte ptr [r8+8],0x10 ; jne +0x11
INJECTED = bytes.fromhex("41f64008107511")
# 2) state gates in front of the key handler; each `je/jne 0x1407427fc` is NOPed so the
#    hook always reaches HandleKey (our copy only)
GATES = bytes.fromhex(
    "4885c0" "0f84e7000000" "0fb680080e0000" "84c0"
    "0f84d8000000" "488b058d80ee00" "0fb680090e0000" "84c0"
    "0f85c2000000"
)
GATE_JUMPS = ("0f84e7000000", "0f84d8000000", "0f85c2000000")


def _patch_all(blob: bytearray, signature: bytes, patch: bytes, label: str) -> int:
    count = 0
    pos = 0
    while True:
        idx = blob.find(signature, pos)
        if idx < 0:
            return count
        blob[idx:idx + len(signature)] = patch
        print(f"[ok] {label} patched at file offset 0x{idx:X}")
        count += 1
        pos = idx + len(signature)


def patch(path: str, mode: str = "bypass") -> int:
    pe = pefile.PE(path)
    base = pe.OPTIONAL_HEADER.ImageBase
    original = open(path, "rb").read()
    blob = bytearray(original)
    sections = [(s.PointerToRawData, s.SizeOfRawData, s.VirtualAddress) for s in pe.sections]
    pe.close()          # pefile keeps the file open; that blocks the write below
    count = 0
    for start, size, vaddr in sections:
        end = start + size
        chunk = blob[start:end]
        hits = 0
        pos = 0
        while True:
            idx = chunk.find(INJECTED, pos)
            if idx < 0:
                break
            blob[start + idx:start + idx + len(INJECTED)] = b"\x90" * len(INJECTED)
            print(f"[ok] injected-key check patched at VA 0x{base + vaddr + idx:X}")
            hits += 1
            pos = idx + len(INJECTED)
        count += hits

        idx = chunk.find(GATES)
        if idx >= 0:
            patched = bytearray(GATES)
            if mode == "voice":
                # always take the hook's voice-matching path: je 0x1407427fc -> jmp
                patched[3:9] = bytes.fromhex("e9e800000090")
            else:
                for jump in GATE_JUMPS:
                    j = GATES.find(bytes.fromhex(jump))
                    patched[j:j + 6] = b"\x90" * 6
            blob[start + idx:start + idx + len(GATES)] = patched
            print(f"[ok] voice-hook state gates patched ({mode}) at VA 0x{base + vaddr + idx:X}")
            count += 1
    if count:
        backup = path + ".orig"
        if not os.path.exists(backup):
            with open(backup, "wb") as fh:
                fh.write(original)
            print(f"[ok] backup at {backup}")
        with open(path, "wb") as fh:
            fh.write(blob)
        print(f"[ok] wrote {len(blob)} bytes")
    else:
        print("[warn] signature not found (already patched?)")
    return count


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mode = "voice" if "--mode=voice" in sys.argv else "bypass"
    runtime = os.path.abspath(args[0])
    path = os.path.join(runtime, "ImeService.exe")
    if not os.path.exists(path):
        print(f"[fail] {path} not found")
        return 1
    patch(path, mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
