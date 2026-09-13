"""Plan B: open the private pipe ourselves and hand the handle to RpcPipe_*.

Evidence so far: the exported client functions never open a pipe (verified with hooks on
CreateFileW/CreateFileA/NtCreateFile), and rpc.dll contains no pipe path literal - so the
caller is expected to open the pipe and pass the HANDLE.

Usage:
    python probe_pipe_direct.py <runtime_dir> [pipe_name]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    pipe = sys.argv[2] if len(sys.argv) > 2 else "\\\\.\\pipe\\ObricIme\\oime-serveR"
    os.add_dll_directory(runtime)

    handle = k32.CreateFileW(pipe, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
    if handle in (None, 0, INVALID_HANDLE_VALUE):
        print(f"[fail] CreateFileW({pipe!r}) -> {handle} err={ctypes.get_last_error()}", flush=True)
        return 1
    print(f"[ok] opened pipe {pipe!r} handle={handle}", flush=True)

    dll = ctypes.CDLL(os.path.join(runtime, "rpc.dll"))

    # the exported functions take a *client object*, not a raw pipe handle:
    # calling them with our handle crashed inside (deref at +0x18C)
    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    client = create(pipe.encode("utf-8"))
    print(f"[call] CreateRpcClient(name) -> {client}", flush=True)

    ensure = dll.RpcPipe_EnsureServerRunning
    ensure.restype = ctypes.c_bool
    ensure.argtypes = [ctypes.c_void_p]
    print(f"[call] EnsureServerRunning(client) -> {ensure(client)}", flush=True)

    for name, restype in (("RpcPipe_GetInputState", ctypes.c_int),
                          ("RpcPipe_SimpleMessage", ctypes.c_bool)):
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = [ctypes.c_void_p]
        try:
            print(f"[call] {name}(client) -> {fn(client)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[crash] {name}: {exc}", flush=True)

    peek = dll.RpcPipe_PeekVoiceCommitUtf8
    peek.restype = ctypes.c_int
    peek.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p, ctypes.c_int]
    buf = ctypes.create_string_buffer(0x40001)
    session = ctypes.c_uint64(0)
    print(f"[call] PeekVoiceCommitUtf8 -> "
          f"{peek(buf, ctypes.byref(session), None, 0x40001)} session={session.value}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
