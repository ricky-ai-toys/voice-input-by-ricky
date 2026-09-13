"""Plan B: what exactly does CreateRpcClient() return?

Hypothesis: the RpcPipe_* exports are the *server* dispatch entry points, while the client is
a C++ object returned by CreateRpcClient with a vtable of methods that are not exported.
This dumps that object: vtable entries resolved back to rpc.dll offsets.

    python inspect_client_object.py <runtime_dir> [pipe_name]
"""
import ctypes
import os
import sys


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    pipe = sys.argv[2] if len(sys.argv) > 2 else "\\\\.\\pipe\\ObricIme\\oime-serveR"
    os.add_dll_directory(runtime)
    dll_path = os.path.join(runtime, "rpc.dll")
    dll = ctypes.CDLL(dll_path)

    import pefile

    pe = pefile.PE(dll_path, fast_load=True)
    image_base = pe.OPTIONAL_HEADER.ImageBase

    create = dll.CreateRpcClient
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_char_p]
    obj = create(pipe.encode("utf-8"))
    print(f"[info] CreateRpcClient({pipe!r}) -> 0x{obj:016X}" if obj else "[fail] null")
    if not obj:
        return 1

    vtable = ctypes.c_void_p.from_address(obj).value
    print(f"[info] vtable @ 0x{vtable:016X}  (rpc.dll base 0x{image_base:016X})")
    if not vtable:
        return 1

    for i in range(12):
        fn = ctypes.c_void_p.from_address(vtable + i * 8).value
        if not fn:
            break
        print(f"[vtable] [{i:2d}] 0x{fn:016X}  (rpc.dll+0x{fn - image_base:X})")

    # first few words of the object itself often carry state (pipe handle, flags)
    words = [ctypes.c_void_p.from_address(obj + i * 8).value for i in range(8)]
    print("[object] " + " ".join(f"{w:016X}" if w else "0" * 16 for w in words))
    return 0


if __name__ == "__main__":
    sys.exit(main())
