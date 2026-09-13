"""Does the engine wait at a short file (declared long) or end the session?

Header declares N seconds but only M seconds are physically present. If the engine
keeps waiting for more bytes, we can pin the reader to real time by growing the file
only as fast as the user speaks.
"""
import base64
import json
import os
import struct
import sys
import time
import wave

import frida

ROOT = r"C:\Users\Ricky\Documents\Codex\2026-09-13\wo\work\doubao-probe"
EXE = os.path.join(ROOT, "runtime", "v0.9.0.0", "ImeService.exe")
SCRIPT = os.path.join(ROOT, "scripts", "extract_text.js")
TTS = os.path.join(ROOT, "audio", "tts_zh_16k.wav")
OUT = os.path.join(ROOT, "audio", "eof_test.wav")

SR = 16000
DECLARED_SEC = 60.0


def main() -> int:
    with wave.open(TTS, "rb") as fh:
        speech = fh.readframes(fh.getnframes())
    data_len = int(DECLARED_SEC * SR * 2)
    with open(OUT, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE")
        fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16))
        fh.write(b"data" + struct.pack("<I", data_len))
        fh.write(speech)          # physically only the speech, no padding
    print(f"physical={len(speech)/32000:.2f}s declared={DECLARED_SEC}s", flush=True)

    with open(SCRIPT, encoding="utf-8") as fh:
        source = fh.read()
    argv = [EXE, "--test-sami", "--wav", OUT]
    pid = frida.spawn(EXE, argv=argv, cwd=os.path.dirname(EXE), stdio="inherit")
    session = frida.attach(pid)
    script = session.create_script(source)
    t0 = time.time()

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        if payload.get("kind") == "feed":
            return
        if payload.get("kind") != "json":
            return
        try:
            doc = json.loads(base64.b64decode(payload.get("b64") or "").decode("utf-8"))
        except Exception:
            return
        for item in doc.get("results") or []:
            text = (item.get("text") or "").strip()
            if text:
                print(f"[{time.time()-t0:5.2f}s] vad={item.get('is_vad_finished')} {text!r}", flush=True)

    script.on("message", on_message)
    script.load()
    frida.resume(pid)

    deadline = time.time() + 20
    while time.time() < deadline:
        out = os.popen(f'tasklist /FI "PID eq {pid}" /NH').read()
        if str(pid) not in out:
            print(f"engine exited after {time.time()-t0:.2f}s (short physical file)", flush=True)
            break
        time.sleep(0.25)
    else:
        print(f"engine STILL RUNNING after 20s -> it waits for more data "
              f"(reader stays pinned to our write rate)", flush=True)
        frida.kill(pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
