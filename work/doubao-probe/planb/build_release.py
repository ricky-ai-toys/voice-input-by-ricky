"""Build the portable release: a patched engine copy + the shell exe, in one folder.

    python build_release.py [--vendor "C:\\Program Files\\DoubaoIME\\versions\\v0.9.0.0"]

Result (no installer, no admin, no TSF registration):

    outputs/VoiceInputByRicky/
      VoiceInputByRicky.exe      shell (hold Right Alt, speak, text is inserted)
      runtime/                   the seller's engine, renamed pipes + uiAccess=false
      data/config.json           settings
      README.md
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_VENDOR = r"C:\Program Files\DoubaoIME\versions\v0.9.0.0"
DEFAULT_OUT = os.path.join(REPO, "outputs", "VoiceInputByRicky")


def run(*argv: str) -> None:
    print(f"[build] {' '.join(argv)}", flush=True)
    subprocess.check_call(list(argv), cwd=HERE)


def build_runtime(vendor: str, runtime: str) -> None:
    if os.path.exists(runtime):
        print(f"[build] removing {runtime}")
        shutil.rmtree(runtime)
    print(f"[build] copying the vendor engine {vendor} -> {runtime}")
    shutil.copytree(vendor, runtime)

    run(sys.executable, "patch_names.py", runtime, "--suffix", "1")
    run(sys.executable, os.path.join(HERE, "..", "scripts", "patch_manifest.py"),
        os.path.join(runtime, "ImeService.exe"))
    # the product triggers with the *real* hotkey, so the vendor's injected-key filters stay
    run(sys.executable, "patch_voicehook.py", runtime, "--mode=none", "--keep-filters")

    removed = 0
    for name in os.listdir(runtime):
        if name.endswith(".orig"):
            os.remove(os.path.join(runtime, name))
            removed += 1
    print(f"[build] removed {removed} .orig backup(s) from the release")


def build_exe(out: str) -> None:
    work = os.path.join(HERE, "build_tmp")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
        "--name", "VoiceInputByRicky", "--distpath", out, "--workpath", work,
        "--specpath", HERE, "--paths", HERE,
        "--exclude-module", "frida",          # dev-only (the product uses the real hotkey)
        "--exclude-module", "numpy",
        "--exclude-module", "sounddevice",
        os.path.join(HERE, "voice_input_app.py"))
    spec = os.path.join(HERE, "VoiceInputByRicky.spec")
    if os.path.exists(spec):
        os.remove(spec)
    shutil.rmtree(work, ignore_errors=True)


def write_extras(out: str) -> None:
    data = os.path.join(out, "data")
    os.makedirs(data, exist_ok=True)
    cfg = os.path.join(data, "config.json")
    if not os.path.exists(cfg):
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"hotkey": "right alt", "show_overlay": True, "paste": True,
                       "commit_timeout": 2.5}, fh, indent=2)
    readme = os.path.join(out, "README.md")
    shutil.copyfile(os.path.join(HERE, "release_README.md"), readme)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", default=DEFAULT_VENDOR)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--skip-exe", action="store_true")
    args = ap.parse_args()

    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    build_runtime(os.path.abspath(args.vendor), os.path.join(out, "runtime"))
    if not args.skip_exe:
        build_exe(out)
    write_extras(out)
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _dn, fn in os.walk(out) for f in fn)
    print(f"[build] {out}  ({total / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
