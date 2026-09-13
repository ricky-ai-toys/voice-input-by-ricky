"""Plan B: child process - call the client API and report what happens.

Run through `run_with_hooks.py`, which hooks CreateFileW/CreateFileA in this process so we
can see exactly which pipe path the client opens (and therefore how the pipe name is
derived when the library has no literal of its own).
"""
import ctypes
import os
import sys

PIPE = "\\\\.\\pipe\\ObricIme\\oime-serveR"


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    os.add_dll_directory(runtime)
    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))
    print(f"[probe] pid={os.getpid()} rpc.dll loaded from {runtime}", flush=True)

    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    client = create(PIPE.encode("utf-8"))
    print(f"[probe] CreateRpcClient({PIPE!r}) -> {client}", flush=True)

    ensure = dll.RpcPipe_EnsureServerRunning
    ensure.restype = ctypes.c_bool
    ensure.argtypes = [ctypes.c_void_p]
    print(f"[probe] EnsureServerRunning -> {ensure(client)}", flush=True)

    peek = dll.RpcPipe_PeekVoiceCommitUtf8
    peek.restype = ctypes.c_int
    peek.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p, ctypes.c_int]
    buf = ctypes.create_string_buffer(0x40001)
    session = ctypes.c_uint64(0)
    n = peek(buf, ctypes.byref(session), None, 0x40001)
    print(f"[probe] PeekVoiceCommitUtf8 -> {n} session={session.value} text={buf.value!r}", flush=True)

    state = dll.RpcPipe_GetInputState
    state.restype = ctypes.c_int
    state.argtypes = [ctypes.c_void_p]
    print(f"[probe] GetInputState -> {state(client)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
