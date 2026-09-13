"""Plan B litmus test: can a plain process drive the engine through rpc.dll?

Runs ONE call variant and prints the raw result, so the caller can try several
signatures in separate processes (a wrong signature can crash the caller).

    python try_ensure_server.py <runtime_dir> <variant>

variants: 0 none-arg, 1 handle-from-CreateRpcClient, 2 string-path-arg
"""
import ctypes
import os
import sys

VARIANTS = {
    0: "EnsureServerRunning() with no args",
    1: "CreateRpcClient() no args, then ensure(handle)",
    2: "EnsureServerRunning(wide pipe name)",
    3: "CreateRpcClient(narrow pipe name) then ensure(handle)",
    4: "CreateRpcClient(wide pipe name) then ensure(handle)",
    5: "CreateRpcClient(private) + RpcPipe_GetInputState(handle)",
    6: "CreateRpcClient(private) + RpcPipe_SimpleMessage(handle)",
    7: "RpcPipe_PeekVoiceCommitUtf8(buffer, &session, ctx, 0x40001)  [recovered signature]",
    8: "CreateRpcClient(private) + EnsureServerRunning + PeekVoiceCommitUtf8",
}


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
    elif variant == 2:
        ensure.argtypes = [ctypes.c_wchar_p]
        pipe = "\\\\.\\pipe\\ObricIme\\oime-server"
        print(f"[call] RpcPipe_EnsureServerRunning({pipe!r}) -> {ensure(pipe)}", flush=True)
    elif variant in (3, 4):
        pipe = "\\\\.\\pipe\\ObricIme\\oime-serveR"
        create = dll.CreateRpcClient
        create.restype = ctypes.c_void_p
        if variant == 3:
            create.argtypes = [ctypes.c_char_p]
            client = create(pipe.encode("utf-8"))
        else:
            create.argtypes = [ctypes.c_wchar_p]
            client = create(pipe)
        print(f"[call] CreateRpcClient({pipe!r}) -> {client}", flush=True)
        ensure.argtypes = [ctypes.c_void_p]
        print(f"[call] RpcPipe_EnsureServerRunning({client}) -> {ensure(client)}", flush=True)
    elif variant in (5, 6):
        pipe = "\\\\.\\pipe\\ObricIme\\oime-serveR"
        create = dll.CreateRpcClient
        create.restype = ctypes.c_void_p
        create.argtypes = [ctypes.c_char_p]
        client = create(pipe.encode("utf-8"))
        print(f"[call] CreateRpcClient({pipe!r}) -> {client}", flush=True)
        if not client:
            print("[fail] no client handle", flush=True)
            return 1
        if variant == 5:
            fn = dll.RpcPipe_GetInputState
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_void_p]
            print(f"[call] RpcPipe_GetInputState({client}) -> {fn(client)}", flush=True)
        else:
            fn = dll.RpcPipe_SimpleMessage
            fn.restype = ctypes.c_bool
            fn.argtypes = [ctypes.c_void_p]
            print(f"[call] RpcPipe_SimpleMessage({client}) -> {fn(client)}", flush=True)
    elif variant == 8:
        pipe = "\\\\.\\pipe\\ObricIme\\oime-serveR"
        create = dll.CreateRpcClient
        create.restype = ctypes.c_void_p
        create.argtypes = [ctypes.c_char_p]
        client = create(pipe.encode("utf-8"))
        ensure = dll.RpcPipe_EnsureServerRunning
        ensure.restype = ctypes.c_bool
        ensure.argtypes = [ctypes.c_void_p]
        print(f"[call] CreateRpcClient -> {client}; EnsureServerRunning -> {ensure(client)}", flush=True)
        fn = dll.RpcPipe_PeekVoiceCommitUtf8
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p, ctypes.c_int]
        buf = ctypes.create_string_buffer(0x40001)
        session = ctypes.c_uint64(0)
        n = fn(buf, ctypes.byref(session), None, 0x40001)
        print(f"[call] PeekVoiceCommitUtf8 -> {n} session={session.value} text={buf.value!r}", flush=True)
    else:
        # signature recovered from tsf-oime-core.dll @0x180015122:
        #   int PeekVoiceCommitUtf8(char* out_buf, uint64_t* session, void* ctx, int max_len)
        fn = dll.RpcPipe_PeekVoiceCommitUtf8
        fn.restype = ctypes.c_int
        fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p, ctypes.c_int]
        buf = ctypes.create_string_buffer(0x40001)
        session = ctypes.c_uint64(0)
        n = fn(buf, ctypes.byref(session), None, 0x40001)
        print(f"[call] RpcPipe_PeekVoiceCommitUtf8(buf, &session, NULL, 0x40001) -> {n}", flush=True)
        print(f"[call] session={session.value} text={buf.value!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
