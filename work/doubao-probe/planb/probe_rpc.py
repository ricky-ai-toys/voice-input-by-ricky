"""Plan B prep: smoke-test the engine's local pipe client (`rpc.dll`).

Loads the *copy's* rpc.dll with ctypes and calls `CreateRpcClient` /
`RpcPipe_EnsureServerRunning`. Those two are the entry points the TSF core uses, and they
are exported with C linkage, so this is the cheapest possible check that a Python-side
client can talk to the engine before investing in signature recovery for the rest.

Calling convention notes (to be confirmed by disassembling tsf-oime-core.dll call sites -
this is exactly what the probe is for):
    CreateRpcClient()                  -> client handle (or NULL)
    RpcPipe_EnsureServerRunning(?)     -> bool

Usage:
    python probe_rpc.py <runtime_dir> [--call]
"""
import argparse
import ctypes
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("runtime_dir")
    ap.add_argument("--call", action="store_true", help="actually invoke the exports")
    args = ap.parse_args()

    runtime_dir = os.path.abspath(args.runtime_dir)
    dll_path = os.path.join(runtime_dir, "rpc.dll")
    if not os.path.exists(dll_path):
        print(f"[error] rpc.dll not found at {dll_path}")
        return 2

    # the engine's own directory must be on the DLL search path for its dependencies
    os.add_dll_directory(runtime_dir)
    dll = ctypes.CDLL(dll_path)

    names = [n for n in (
        "CreateRpcClient", "DestroyRpcClient", "RpcPipe_EnsureServerRunning",
        "RpcPipe_FocusIn", "RpcPipe_FocusOut", "RpcPipe_UpdateHostContextUtf8",
        "RpcPipe_SetCursorPos", "RpcPipe_KeyEvent", "RpcPipe_KeyDown", "RpcPipe_KeyUp",
        "RpcPipe_PeekVoiceCommitUtf8", "RpcPipe_AckVoiceCommit", "RpcPipe_SimpleMessage",
    ) if hasattr(dll, n)]
    print(f"[ok] loaded {os.path.basename(dll_path)}")
    print(f"[ok] usable exports: {', '.join(names)}")
    missing = [n for n in ("CreateRpcClient", "RpcPipe_EnsureServerRunning",
                           "RpcPipe_PeekVoiceCommitUtf8", "RpcPipe_AckVoiceCommit")
               if not hasattr(dll, n)]
    if missing:
        print(f"[warn] expected exports missing: {', '.join(missing)}")
        return 1

    if not args.call:
        print("[info] dry run - re-run with --call to invoke CreateRpcClient/EnsureServerRunning")
        return 0

    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = []
    client = create()
    print(f"[call] CreateRpcClient() -> {client}")

    ensure = dll.RpcPipe_EnsureServerRunning
    ensure.restype = ctypes.c_bool
    ensure.argtypes = [ctypes.c_void_p]
    ok = ensure(client)
    print(f"[call] RpcPipe_EnsureServerRunning(client) -> {ok}")
    return 0 if client else 1


if __name__ == "__main__":
    sys.exit(main())
