# Plan B - resident engine + local pipe (prep notes)

Prepared while debugging the file-based path. This is the groundwork for a fork that gets
native-level latency and unlimited dictation length.

## Why Plan B

Everything we learned about `ImeService.exe --test-sami` (the current transport):

| Finding | Evidence |
| --- | --- |
| It reads a **complete** WAV; EOF ends the session | `scripts/exp_eof_wait.py`: declared 60 s, physically 4.47 s → engine exits at 5.6 s |
| Read rate is **not** real-time | diag runs: 0.6x-2.5x real time; silence is consumed at full speed, speech is throttled by server flow control |
| That makes true streaming unreliable | writing "at the live edge" punched silent gaps; tight files ended the session early; long padding let the reader outrun the mic |
| Result | short/medium utterances work (~1-2 s tail when warm), >~40 s is unreliable |

The native IME does not use files at all: the engine runs as a **long-lived process**, captures
the microphone itself (`waveIn`, 16 kHz mono), streams to the cloud over a **warm connection
pool**, and hands the recognized text to the foreground client through a **local named pipe**.

## What we already know about that path

* Pipe server (created by `ImeService.exe`): `\\.\pipe\ObricIme\oime-server`
  plus `\\.\pipe\ObricIme\oime-server-tsf-log` and `\\.\pipe\DoubaoIme\settings-rpc`.
* Client library: `rpc.dll` exports 32 C-style functions, including
  `CreateRpcClient`, `DestroyRpcClient`, `RpcPipe_EnsureServerRunning`, `RpcPipe_FocusIn`,
  `RpcPipe_FocusOut`, `RpcPipe_UpdateHostContextUtf8`, `RpcPipe_SetCursorPos`,
  `RpcPipe_KeyEvent` / `RpcPipe_KeyDown` / `RpcPipe_KeyUp`,
  `RpcPipe_PeekVoiceCommitUtf8`, `RpcPipe_AckVoiceCommit`, `RpcPipe_SimpleMessage(Ex)`.
* `tsf-oime-core.dll` imports 23 of them - the call sites are the Rosetta stone for the
  signatures (each export's arguments can be recovered from how the core sets them up).
* The engine's own recorder strings (`voice recorder start begin requested_device_index=...
  sample_rate=16000 channels=1 bits=16`, `[MicList]/[MicPick]`) live in `ImeService.exe`,
  i.e. **the server can capture the microphone by itself** - the client may only need to say
  "start/stop voice".
* Runtime facts needed: engine runs fine as a standard user once the copy's manifest says
  `uiAccess="false"`; applog/TTNet state is per-user under `%APPDATA%\DoubaoIme`; an idle cloud
  stream is dropped after ~29 s (the resident server re-establishes it).

## Prep artefacts to build (in a fork)

1. `patch_pipe_name.py` - rewrite `\\.\pipe\ObricIme\oime-server` inside the **copy** of
   `ImeService.exe` and `rpc.dll` to a private name (same length, e.g. `...\oime-serveR`) so the
   resident server never collides with an installed Doubao IME on the same machine.
2. `probe_rpc.py` - load the copy's `rpc.dll` with ctypes, call
   `CreateRpcClient` → `RpcPipe_EnsureServerRunning`, and report the returned handles; this is
   the cheapest possible smoke test of the pipe route.
3. Signature recovery - disassemble `tsf-oime-core.dll` call sites for the four functions that
   matter (`EnsureServerRunning`, `KeyEvent`, `PeekVoiceCommitUtf8`, `AckVoiceCommit`) and write
   the argument shapes into a header/spec file.
4. Minimal end-to-end experiment - start the copy in server mode (no `--test-sami`), ask it to
   start voice, speak, then poll `RpcPipe_PeekVoiceCommitUtf8`; success = text arrives without
   any WAV file being involved.
5. Fallback - keep the current file-based build as the shipped product until the pipe route is
   proven; the warm-session work (`StreamSession`) stays useful as the fallback transport.

## Expected outcome

| | current (file) | Plan B (resident engine) |
| --- | --- | --- |
| first text | ~1.5 s after speech start | < 1 s |
| release → text | 1-2 s typical, variable | ~0.3-0.6 s, consistent |
| dictation length | ~40 s limit | unlimited |
| disk | writes 1-2 MB per utterance | none |
| admin rights | none | none |

No administrator rights are involved anywhere in Plan B: it runs the same user-owned copy of
the engine, only driven through its local pipe instead of a temporary WAV file.

---

## Litmus test result (2026-09-13)

```
> python work\doubao-probe\planb\try_ensure_server.py <runtime> 0
[call] RpcPipe_EnsureServerRunning() -> True          # no arguments needed
> python ... <runtime> 2
[call] RpcPipe_EnsureServerRunning('\\.\pipe\ObricIme\oime-server') -> True
> python ... <runtime> 1
CreateRpcClient() crashed the caller                        # not a real export shape
```

What this proves:
* a plain Python process can load the copy's `rpc.dll` and bind its exports;
* `RpcPipe_EnsureServerRunning()` takes **no arguments** and returns true - the client-side
  bootstrap into the engine works, and the pipe surface is reachable from user code.

What it does **not** prove yet:
* no new engine process appeared, so the call found the *already running* installed engine
  (PID 21644) instead of starting our copy's server. On a machine without the official IME the
  server has to be started by us - which is exactly what `patch_pipe_name.py` enables (private
  pipe name, then start the copy in server mode and call `EnsureServerRunning` again).

Next step for the fork: patch the pipe name in a scratch copy, start that copy as a server,
re-run the litmus test against the private pipe, then recover the signatures of
`RpcPipe_KeyEvent`, `RpcPipe_PeekVoiceCommitUtf8` and `RpcPipe_AckVoiceCommit`.
