# Doubao Voice Input (portable, no admin)

Hold **Right Alt**, speak, release - the text is typed at your cursor.
Everything lives in this folder: **no installer, no administrator rights, no changes
to Windows input methods.**

---

## Quick start

1. Double-click `Start Doubao Voice Input.cmd` (no UAC prompt appears).
2. A short "Ready - hold Right Alt and speak" badge appears, then it stays resident.
3. In any text box: **hold Right Alt → speak → release**.
   - A status strip shows `● Listening...` while you speak and streams the text as it is
     recognized, then switches to `… Transcribing`.
   - The finished text is pasted at the cursor.
4. To quit: end `DoubaoVoice.exe` in Task Manager.

Latency measured on a ~4 s utterance: text starts appearing **~1.5 s after you start
speaking**, and the final text lands **~1.1-1.7 s after you release** (measured 1.7 s for
Chinese and 1.5 s for English with the packaged build). See "Latency notes" below.

---

## Languages

The engine auto-detects the language; no setting is needed. Verified in practice:

| Language | Result |
| --- | --- |
| Mandarin | `我确认程序能用，而且不错。请给出下一步的升级迭代规划。` |
| Cantonese speech | `请问听到吗？我讲广东话，一二三。` |
| English | `Good morning, everybody. Today is Sunday. I'm going to go swimming with my daughter...` |

Mandarin, Cantonese and English can be mixed in one utterance.

---

## Layout

```
DoubaoVoicePortable\
  app\                        main program (bundled runtime, audio libs, frida)
  runtime\v0.9.0.0\           the vendor's own speech engine (copied, patched, pinyin
                              dictionaries removed - not needed for voice)
  data\                       config.json, logs\app.log, live_*.wav scratch files
  Start Doubao Voice Input.cmd
```

The folder can be copied to a USB stick or another PC as-is.
**Verified**: it also works on a machine where Doubao was never installed (tested with an
empty user profile) - no account, no login, no leftover state required.

---

## Requirements

| Item | Requirement |
| --- | --- |
| OS | Windows 10 / 11 64-bit |
| Rights | standard user (verified running at Medium integrity, never elevated) |
| Microphone | any input device |
| Network | access to `*.doubao.com` (recognition runs in the cloud) |
| Disk | ~300 MB |

---

## Configuration (`data\config.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `right alt` | also: `left alt`, `right ctrl`, `left ctrl`, `caps lock`, ... |
| `suppress_hotkey` | `false` | `true` = the key is swallowed so it cannot reach the app behind |
| `min_duration_sec` | `0.35` | shorter presses are ignored |
| `final_timeout_sec` | `8.0` | how long to wait for the final text after release |
| `restore_clipboard` | `true` | restore the previous clipboard content after pasting |
| `show_overlay` | `true` | show the listening/transcribing strip |

Restart the program after editing.

### Start with Windows (optional)

```
app\DoubaoVoice.exe --install-autostart     (creates a shortcut in your Startup folder)
app\DoubaoVoice.exe --remove-autostart      (removes it)
```

Both are per-user and need no admin rights.

---

## How it works

1. A low-level keyboard hook watches Right Alt press/release.
2. On press the program immediately starts the vendor engine against a pre-allocated WAV
   and writes your microphone audio into it **while you speak** (the engine reads the file
   progressively and streams recognition results back).
3. On release the WAV is cut to the real length so the engine hits end-of-stream, and the
   final text is collected.
4. The text goes to the clipboard, a synthetic Ctrl+V pastes it at the cursor, and the old
   clipboard content is restored.

The vendor's `ImeService.exe` ships with `uiAccess="true"`, which makes Windows refuse to
start it for a standard user. In this **copy only**, that one attribute was rewritten
in-place to `false` (same byte length), which is why the program must use its own
`runtime\` folder.

---

## Latency notes

Measured on this machine, 4.5 s Chinese utterance:

| Stage | v1 (one-shot) | v2 (streaming) | v3 (pre-warmed, current) |
| --- | --- | --- | --- |
| connection setup | after release (~1.7 s) | overlapped with speech | **already done before you press** |
| first text visible | after release | ~2.2 s after speech start | **~1.5 s after speech start** |
| final text after release | ~5 s | ~4.3-5.7 s | **~1.1-1.7 s** |

How the tail was cut: the program keeps a **pre-warmed engine session** connected in the
background (a long silent WAV the engine reads at ~1x). It watches the engine's own
"frames consumed" log to know the exact read position, and when you press the key it
writes your audio *at that live edge* instead of at the beginning of the file. On release
the file is cut just after your audio, so the engine hits end-of-stream almost immediately
and only the finalize step (VAD + second pass, ~1 s) is left.

Known variance: if the cloud connection stalls, the reader can fall behind and the tail
grows to ~5 s; and with pure silence (no speech at all) there is no VAD event, so the
session ends only at end-of-stream.

---

## Limitations

- **Needs network** - recognition happens in Doubao's cloud.
- **Cannot paste into elevated windows** (Windows UIPI).
- **Private vendor interface** - a future Doubao IME update may move the two patch points;
  this is why `runtime\` is pinned to version 0.9.0.0 and never auto-updates.
- **Engine logs desensitize text** (Chinese becomes `X`); the program does not read logs,
  it reads the result structure, so the pasted text is the real one.
- **Company policy**: audio is uploaded to Doubao's cloud - check your employer's rules,
  and note that running unauthorised software on a work device may itself violate policy.

---

## Uninstall

1. End `DoubaoVoice.exe`.
2. Delete the `DoubaoVoicePortable` folder.
3. Optional: delete `%APPDATA%\DoubaoIme` (device cache; it is regenerated if needed).

No services, scheduled tasks, machine-level registry keys or input-method entries are
created.
