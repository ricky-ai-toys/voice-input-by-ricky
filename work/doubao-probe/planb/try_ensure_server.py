"""Plan B litmus test: can a plain process drive the engine through rpc.dll?

Runs ONE call variant and prints the raw result, so the caller can try several
signatures in separate processes (a wrong signature can crash the caller).

    python try_ensure_server.py <runtime_dir> <variant>

variants: 0 none-arg, 1 handle-from-CreateRpcClient, 2 string-path-arg
"""
import ctypes
import os
import sys

VARIANTS = {0: "no args", 1: "client handle from CreateRpcClient", 2: "wide-string path arg"}


def main() -> int:
    runtime_dir = os.path.abspath(sys.argv[1])
    variant = int(sys.argv[2])
    os.add_dll_directory(runtime_dir)
    dll = ctypes.CDLL(os.path.join(runtime_dir, "rpc.dll"))
    print(f"[env] variant {variant}: {VARIANTS[variant]}", flush=True)

    ensure = dll.RpcPipe_EnsureServerRunning
    ensure.restype = ctypes.c_bool

    if variant == 0:
        ensure.argtypes = []
        print(f"[call] RpcPipe_EnsureServerRunning() -> {ensure()}", flush=True)
    elif variant == 1:
        create = dll.CreateRpcClient
        create.restype = ctypes.c_void_p
        create.argtypes = []
        client = create()
        print(f"[call] CreateRpcClient() -> {client}", flush=True)
        ensure.argtypes = [ctypes.c_void_p]
        print(f"[call] RpcPipe_EnsureServerRunning({client}) -> {ensure(client)}", flush=True)
    else:
        ensure.argtypes = [ctypes.c_wchar_p]
        pipe = "\\\\.\\pipe\\ObricIme\\oime-server"
        print(f"[call] RpcPipe_EnsureServerRunning({pipe!r}) -> {ensure(pipe)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
