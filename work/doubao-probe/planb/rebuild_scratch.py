"""Plan B: rebuild the private engine copy from the pristine vendor binary.

The copy needs three byte-neutral patches plus one code patch; running them in the wrong
order (or forgetting one) is the easiest way to waste an hour, so the chain lives here:

    python rebuild_scratch.py <runtime_dir>

    1. restore every target from its `.orig` backup
    2. patch_names.py    pipe + mutex names  -> private identity (oime-serve1, settings-rp1)
    3. patch_manifest.py uiAccess="true"     -> "false" (startable as a standard user)
    4. patch_voicehook.py drop injected-key filter + the voice-hook state gates
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = ("ImeService.exe", "rpc.dll", "DoubaoIme.Settings.NativeRuntime.dll",
           "tsf-oime-core.dll")


def run(*argv: str) -> int:
    print(f"[run] {' '.join(argv)}", flush=True)
    return subprocess.call(list(argv), cwd=HERE)


def main() -> int:
    runtime = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                              os.path.join(HERE, "scratch"))
    for name in TARGETS:
        backup = os.path.join(runtime, name + ".orig")
        target = os.path.join(runtime, name)
        if os.path.exists(backup):
            with open(backup, "rb") as fh:
                data = fh.read()
            with open(target, "wb") as fh:
                fh.write(data)
            print(f"[restore] {name} <- {name}.orig")
    run(sys.executable, "patch_names.py", runtime, "--suffix", "1")
    run(sys.executable, os.path.join("..", "scripts", "patch_manifest.py"),
        os.path.join(runtime, "ImeService.exe"))
    run(sys.executable, "patch_voicehook.py", runtime)
    return 0


if __name__ == "__main__":
    sys.exit(main())
