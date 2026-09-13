"""Experiment: pre-warmed session.

Start the engine on a long silent WAV so it has a hot cloud connection, let it
read for a while, then write speech at the reader's *live* position. On release
we cut the file just after the speech so the reader hits EOF almost immediately.

If this works the post-release tail should collapse to the finalize step only
(~1 s), with no connection setup and no backlog.
"""
import base64
import json
import os
import struct
import sys
import threading
import time
import wave

import frida

ROOT = r"C:\Users\Ricky\Documents\Codex\2026-09-13\wo\work\doubao-probe"
EXE = os.path.join(ROOT, "runtime", "v0.9.0.0", "ImeService.exe")
SCRIPT = os.path.join(ROOT, "scripts", "extract_text.js")
TTS = os.path.join(ROOT, "audio", "tts_zh_16k.wav")
OUT = os.path.join(ROOT, "audio", "warm_test.wav")

SR = 16000
BPS = SR * 2
HEADER = 44
PREALLOC_SEC = 300.0
IDLE_BEFORE = 3.0          # idle time before the user presses the key
RATE = 1.0                 # assumed file-read rate (bytes/s -> 1x real time)
LEAD_SEC = 0.30            # write slightly ahead of the estimated reader
TAIL_SEC = 0.30            # extra silence kept after release


def wav_header(data_len: int) -> bytes:
    return (
        b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SR, SR * 2, 2, 16)
        + b"data" + struct.pack("<I", data_len)
    )


def main() -> int:
    with wave.open(TTS, "rb") as fh:
        speech = fh.readframes(fh.getnframes())
    print(f"speech = {len(speech)/BPS:.2f}s", flush=True)

    total = int(PREALLOC_SEC * BPS)
    with wave.open(OUT, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(b"\x00" * total)

    timings = {"final_at": None}
    with open(SCRIPT, encoding="utf-8") as fh:
        source = fh.read()

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
            if not text:
                continue
            now = time.time()
            if item.get("is_vad_finished"):
                timings["final_at"] = now
                print(f"[{now - t_resume:6.2f}s] FINAL {text!r}", flush=True)
            else:
                print(f"[{now - t_resume:6.2f}s] interim {text!r}", flush=True)

    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    t_resume = time.time()
    print(f"[exp] engine running; warming for {IDLE_BEFORE}s", flush=True)

    time.sleep(IDLE_BEFORE)

    # where is the reader now?
    pos = int((time.time() - t_resume) * RATE * BPS)
    write_at = max(0, pos - LEAD_SEC * BPS)
    print(f"[exp] estimated reader position={pos/BPS:.2f}s -> writing at {write_at/BPS:.2f}s", flush=True)

    with open(OUT, "r+b") as fh:
        fh.seek(HEADER + write_at)
        fh.write(speech)
    t_speech_written = time.time()
    print(f"[exp] speech written ({len(speech)/BPS:.2f}s)", flush=True)

    # let the user "release" right after the utterance
    time.sleep(len(speech) / BPS)
    t_release = time.time()
    end = write_at + len(speech) + int(TAIL_SEC * BPS)
    with open(OUT, "r+b") as fh:
        fh.seek(0)
        fh.write(wav_header(end))
        fh.truncate(HEADER + end)
    print(f"[exp] released: truncated to {end/BPS:.2f}s, reader still behind "
          f"by ~{(write_at + len(speech)) / BPS - (t_release - t_resume) * RATE:.2f}s", flush=True)

    deadline = time.time() + 25
    while time.time() < deadline and timings["final_at"] is None:
        time.sleep(0.1)
    if timings["final_at"]:
        print(f"[exp] release->final = {(timings['final_at'] - t_release)*1000:.0f} ms", flush=True)
    else:
        print("[exp] no final text within 25s", flush=True)
    try:
        frida.kill(pid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
