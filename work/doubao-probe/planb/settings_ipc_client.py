"""Plan B: talk to the engine's settings channel.

Captured from `DoubaoImeSettings.exe` (see capture_settings_ipc.py): the settings RPC is

    u32 length | JSON  {"version":1,"requestId":"<32 hex>","method":"settings.get",...}

over `\\.\pipe\DoubaoIme\settings-rpc` (our copy renames it to `...settings-rp1`). The voice
hotkey is gated on the "voice tryout" state that this channel flips, so this tool is how a
plain client can arm the engine's own push-to-talk.

    python settings_ipc_client.py scratch getRuntimeStatus
    python settings_ipc_client.py scratch voiceTryout true
    python settings_ipc_client.py scratch raw '{"version":1,"method":"settings.get"}'
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
import uuid

PIPE = "\\\\.\\pipe\\DoubaoIme\\settings-rp1"

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                            wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                          ctypes.c_void_p]
k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                         ctypes.c_void_p]
k32.PeekNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                              ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)]


class SettingsPipe:
    def __init__(self, name: str = PIPE, timeout: float = 10.0):
        deadline = time.time() + timeout
        self.handle = None
        last = 0
        while time.time() < deadline:
            handle = k32.CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, None,
                                     OPEN_EXISTING, 0, None)
            if handle not in (None, 0, INVALID_HANDLE_VALUE):
                self.handle = handle
                return
            last = ctypes.get_last_error()
            if last not in (2, 231):
                break
            time.sleep(0.1)
        raise OSError(f"CreateFileW({name!r}) failed err={last}")

    def call(self, request: dict, timeout: float = 5.0) -> dict | None:
        body = json.dumps(request, separators=(",", ":")).encode("utf-8")
        header = len(body).to_bytes(4, "little")
        written = wt.DWORD(0)
        buf = ctypes.create_string_buffer(header + body, len(header) + len(body))
        if not k32.WriteFile(self.handle, buf, len(header) + len(body),
                             ctypes.byref(written), None):
            raise OSError(f"WriteFile failed err={ctypes.get_last_error()}")
        deadline = time.time() + timeout
        while time.time() < deadline:
            avail = wt.DWORD(0)
            k32.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(avail), None)
            if avail.value >= 4:
                size = wt.DWORD(0)
                head = ctypes.create_string_buffer(4)
                k32.ReadFile(self.handle, head, 4, ctypes.byref(size), None)
                length = int.from_bytes(head.raw[:4], "little")
                payload = b""
                while len(payload) < length:
                    chunk = ctypes.create_string_buffer(length - len(payload))
                    got = wt.DWORD(0)
                    k32.ReadFile(self.handle, chunk, length - len(payload),
                                 ctypes.byref(got), None)
                    payload += chunk.raw[:got.value]
                try:
                    return json.loads(payload.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    print(f"[warn] response is not JSON: {payload[:200]!r}")
                    return None
            time.sleep(0.05)
        return None

    def close(self) -> None:
        if self.handle:
            k32.CloseHandle(self.handle)


def request(method: str, payload: dict | None = None) -> dict:
    body = {"version": 1, "requestId": uuid.uuid4().hex, "method": method}
    if payload is not None:
        body["payload"] = payload
    return body


def main() -> int:
    runtime = os.path.abspath(sys.argv[1])
    action = sys.argv[2] if len(sys.argv) > 2 else "getRuntimeStatus"
    if action == "voiceTryout":
        active = (sys.argv[3].lower() in ("1", "true", "yes")) if len(sys.argv) > 3 else True
        calls = [request("settings.setVoiceTryoutActive",
                         {"active": active, "cookie": int(time.time())})]
    elif action == "raw":
        calls = [json.loads(sys.argv[3])]
    else:
        calls = [request(f"settings.{action}")]

    pipe = SettingsPipe()
    for call in calls:
        print(f"[send] {json.dumps(call)}")
        response = pipe.call(call)
        print(f"[recv] {json.dumps(response) if response is not None else 'None'}")
    pipe.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
