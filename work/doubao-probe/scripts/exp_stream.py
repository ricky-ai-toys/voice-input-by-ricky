"""Experiment: is the engine reading the WAV progressively while it runs?

Builds an 8 s silent WAV, starts `--test-sami` on it, and 1.2 s later writes the
TTS speech into the region [4.0 s, 5.4 s) - i.e. ahead of the reader if the
engine streams at roughly real time. If the sentence comes back, the engine
reads progressively and a streaming design is viable.
"""
import base64
import json
import os
import sys
import threading
import time
import wave

import frida

ROOT = r"C:\Users\Ricky\Documents\Codex\2026-09-13\wo\work\doubao-probe"
EXE = os.path.join(ROOT, "runtime", "v0.9.0.0", "ImeService.exe")
SCRIPT = os.path.join(ROOT, "scripts", "extract_text.js")
TTS = os.path.join(ROOT, "audio", "tts_zh_16k.wav")
OUT = os.path.join(ROOT, "audio", "stream_test.wav")

SR = 16000
BYTES_PER_SEC = SR * 2
TOTAL_SEC = 8.0
WRITE_AT_SEC = 4.0
WRITE_DELAY = 1.2


def build_wav() -> bytes:
    with wave.open(OUT, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(b"\x00" * int(TOTAL_SEC * BYTES_PER_SEC))
    with wave.open(TTS, "rb") as fh:
        return fh.readframes(fh.getnframes())


def main() -> int:
    speech = build_wav()
    print(f"speech bytes={len(speech)} ({len(speech)/BYTES_PER_SEC:.2f}s)", flush=True)

    events = []

    def writer():
        time.sleep(WRITE_DELAY)
        try:
            with open(OUT, "r+b") as fh:
                fh.seek(int(WRITE_AT_SEC * BYTES_PER_SEC))
                fh.write(speech)
                fh.flush()
            events.append(("write-ok", time.time()))
            print(f"[exp] wrote speech at {WRITE_AT_SEC}s after {WRITE_DELAY}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            events.append(("write-fail", str(exc)))
            print(f"[exp] WRITE FAILED: {exc}", flush=True)

    with open(SCRIPT, encoding="utf-8") as fh:
        source = fh.read()

    t0 = time.time()
    argv = [EXE, "--test-sami", "--wav", OUT]
    pid = frida.spawn(EXE, argv=argv, cwd=os.path.dirname(EXE), stdio="inherit")
    session = frida.attach(pid)
    script = session.create_script(source)

    def on_message(message, data):  # noqa: ANN001
        if message.get("type") != "send":
            return
        payload = message.get("payload") or {}
        if payload.get("kind") != "json":
            return
        try:
            doc = json.loads(base64.b64decode(payload.get("b64") or "").decode("utf-8"))
        except Exception:
            return
        for item in doc.get("results") or []:
            text = (item.get("text") or "").strip()
            if text:
                print(f"[{time.time()-t0:6.2f}s] interim={'Y' if item.get('is_interim') else 'N'} "
                      f"vad={item.get('is_vad_finished')} text={text!r}", flush=True)

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(f"[exp] engine resumed at t={time.time()-t0:.2f}s", flush=True)

    threading.Thread(target=writer, daemon=True).start()

    deadline = time.time() + 40
    while time.time() < deadline:
        out = os.popen(f'tasklist /FI "PID eq {pid}" /NH').read()
        if str(pid) not in out:
            print(f"[exp] engine exited at t={time.time()-t0:.2f}s", flush=True)
            break
        time.sleep(0.5)
    else:
        frida.kill(pid)

    print(f"[exp] events: {events}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
