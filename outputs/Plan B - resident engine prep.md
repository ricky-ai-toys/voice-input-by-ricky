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

---

## Local progress (2026-09-13, first Plan B session)

**Milestone 1 - resident server with a private identity: WORKING**

Two fixed names keep a second engine from coexisting with the official IME; both are
byte-length preserving rewrites (`planb/patch_names.py`):

| Name | Original | Ours |
| --- | --- | --- |
| RPC pipe (ASCII) | `\\.\pipe\ObricIme\oime-server` | `\\.\pipe\ObricIme\oime-serveR` |
| single instance (UTF-16LE) | `ObricImeServerSingleInstance` | `ObricImeServerSingleInstancR` |

Evidence (`planb/evidence/server_private_pipe_run.log`): the copy starts, initialises the
engine, and logs

```
[server_main.cpp:967] ServerRpcThread Run addr: \\.\pipe\ObricIme\oime-serveR
```

and keeps running **side by side with the installed engine** (our PID kept alive while the
official IME still owned the original pipe).

**Milestone 1b - the server path needs the full dictionaries.** With the trimmed package
(no `files/data/dict`) the server dies in `shell_impl.cpp:178 Attach new mode fail` ->
`controller.cpp:772 shell init failed`. The file-test path does not need them; the resident
server does. So the Plan B package keeps the full runtime (~266 MB), not the 110 MB trim.

**Milestone 2 - client library: partially proven, round trip still open.**

From plain Python (`planb/try_ensure_server.py`):

```
CreateRpcClient('\\\\.\\pipe\\ObricIme\\oime-serveR') -> 2442380826800   (handle)
RpcPipe_GetInputState(handle)                         -> 0
RpcPipe_SimpleMessage(handle)                         -> True
```

Control test: `CreateRpcClient('...oime-does-not-exist')` **also** returns a handle and
`EnsureServerRunning` also returns True, and our server's log shows **zero** `PipeCall`
entries - so creation is lazy and nothing has actually reached our server yet. The client
signatures are therefore still unknown.

Next steps (in order):
1. Recover the real argument shapes: extend `planb/find_callsite.py` to also catch
   register-indirect calls (`mov reg, qword ptr [rip+disp]` followed by `call reg`), then read
   the setup for `RpcPipe_KeyEvent`, `RpcPipe_UpdateHostContextUtf8`,
   `RpcPipe_PeekVoiceCommitUtf8`, `RpcPipe_AckVoiceCommit` in `tsf-oime-core.dll`.
2. Instrument our own server (`rpc_server_impl.cpp` logs `PipeCall begin op=%u req_size=%u`)
   to prove when a frame actually arrives.
3. First real target: make the server report "client connected / op received", then drive
   key events and read the voice commit text.

### Milestone 3 - signature recovery works, round trip still open

`find_callsite.py` now also resolves **delay-load thunks** (`jmp qword ptr [IAT]`), which is
how the core really reaches `rpc.dll`. That immediately produced the one call site of
`RpcPipe_PeekVoiceCommitUtf8` in `tsf-oime-core.dll` (evidence:
`planb/evidence/peek_callsite.txt`):

```
0x1800150F8  mov   [rsp+0x40], rbp                 ; session id (in/out)
0x1800150FD  lea   rcx, [rip+0x1a3efc]            ; std::string buffer (SSO aware)
0x18001510C  cmovae rcx, [rip+0x1a3eec]           ; -> char* output buffer
0x180015114  mov   r9d, 0x40001                   ; max length (0x40001)
0x18001511A  mov   r8,  qword ptr [rdi]            ; context pointer
0x18001511D  lea   rdx, [rsp+0x40]                ; &session
0x180015122  call  RpcPipe_PeekVoiceCommitUtf8
0x180015127  movsxd rsi, eax                       ; returns int bytes; >0 means text
```

so the shape is:

```c
int RpcPipe_PeekVoiceCommitUtf8(char* out_utf8, uint64_t* session,
                                const void* ctx, int max_len);
```

`AckVoiceCommit`/`KeyEvent`/`UpdateHostContextUtf8` can be recovered the same way.

Calling it from Python against the private pipe returns **-1** and our server still shows zero
`PipeCall` entries, i.e. the client never really connected:

```
CreateRpcClient('...oime-serveR') -> handle ; RpcPipe_EnsureServerRunning(handle) -> True
RpcPipe_PeekVoiceCommitUtf8(buf, &session, NULL, 0x40001) -> -1
```

Open questions for the next session (in order):
1. What `CreateRpcClient` really takes - a name alone yields a lazily filled client (a bogus
   name behaves identically), so either the name must be paired with a mode/callback, or the
   connection is opened by a different entry point.
2. Whether `EnsureServerRunning` uses the supplied handle or a default pipe name (it returned
   True even for a non-existent name, so it is not proof of connection).
3. Which call performs the actual `CreateFile` on the pipe - hooking `CreateFileW` inside a
   probe process while calling the API will answer this in one step.

### Milestone 4 - the client is a C++ object, not the exported RpcPipe_* functions

Evidence gathered this session (all reproducible with the scripts in `planb/`):

1. **The client never opens the pipe.** With `CreateFileW`, `CreateFileA` and `ntdll!NtCreateFile`
   hooked inside the probe process (`run_with_hooks.py`), a full sequence of
   `CreateRpcClient` / `EnsureServerRunning` / `PeekVoiceCommitUtf8` / `GetInputState` /
   `SimpleMessage` produced **42 opens, all of them ordinary Python files - zero pipes**
   (`evidence/client_opens_all.txt`). So the exported `RpcPipe_*` entry points are *not* the
   client API - they match the server-side dispatch logging seen in `rpc_server_impl.cpp`
   ("PipeDispatch no callback op={}").
2. **`CreateRpcClient(name)` returns a C++ object with a vtable** (`inspect_client_object.py`):
   12+ method pointers, plus a heap pointer as the second word. That vtable is the real client
   API (`evidence/client_vtable.txt`).
3. **Our private pipe is reachable from user code**: `CreateFileW('\\.\pipe\ObricIme\oime-serveR')`
   succeeds against our resident copy (handle returned), i.e. the server side is ready; only
   the client side is still unidentified.
4. Passing a raw pipe handle where a client object is expected crashes with
   `access violation reading 0x18C` - confirming the first argument is a client object.

Next concrete step: resolve the `call qword ptr [rip+disp]` targets inside each vtable method at
runtime (read the IAT slot) to find which method calls `kernel32!CreateFileW` - that method is
`Connect(pipe_name)`. Then drive it from Python together with the send/peek methods.

### Milestone 5 - the vendor client runs inside our Python process and connects to the engine

This is the breakthrough for Plan B. Instead of reimplementing the rpc client we now **host the
vendor's own TSF text service**, which contains the entire client logic (pipe transport,
microphone capture, cloud streaming, text commit).

`probe_activate_tip.py` (run through `run_with_hooks.py`):

```
[1] ITfThreadMgr hr=0x00000000
[2] ITfThreadMgr::Activate hr=0x00000000 tid=32
[3] CreateDocumentMgr hr=0x00000000
[4] CoCreateInstance(ITfTextInputProcessorEx) hr=0x80004002   (E_NOINTERFACE, expected)
[4b] base ITfTextInputProcessor hr=0x00000000                (the service exposes the base iface)
[hook] CreateFileA('\\.\pipe\ObricIme\oime-server')
[hook] CreateFileA('\\.\pipe\ObricIme\oime-server-tsf-log')
[5] Activate hr=0x00000000
```

So from plain Python: `CoCreateInstance(CLSID_TF_ThreadMgr)` -> `Activate` -> `CreateDocumentMgr`
-> `CoCreateInstance(Doubao TIP CLSID)` -> `ITfTextInputProcessor::Activate(thread_mgr, tid)`
works, and the service immediately opens the engine pipes (18 pipe opens observed in that run).
The transport we could not find earlier lives inside `tsf-oime-core.dll`, and it is now running
in-process with us.

Remaining work, in order:
1. **Feed the voice trigger**: QueryInterface the service for `ITfKeyEventSink` and call
   `OnTestKeyDown/OnKeyDown/OnKeyUp` with `VK_RMENU` (Right Alt) to start/stop recording.
2. **Give it a place to commit text**: implement a minimal `ITextStoreACP` and create a
   context via `ITfDocumentMgr::CreateContext` (about 20 vtable methods; the voice text is
   committed through `SetText`/`InsertAtSelection`). This is the text we paste.
3. **Productise**: register the CLSID per-user (`HKCU\Software\Classes\CLSID\{9D2B2E2B-...}`)
   pointing at our portable copy, redirect the core's `VersionDir` (HKCU or a patched stub) and
   rename the pipe in the copy so it talks to *our* resident engine - all without admin rights.

Interface ids for the harness (`planb/tsf_iids.txt`, extracted by `fetch_tsf_iids.py` from
Wine's msctf.idl because this machine has no Windows SDK):

```
ITfThreadMgr                     {AA80E801-2021-11D2-93E0-0060B067B86E}
ITfDocumentMgr                   {AA80E7F4-2021-11D2-93E0-0060B067B86E}
ITfContext                       {AA80E7FD-2021-11D2-93E0-0060B067B86E}
ITfTextInputProcessor            {AA80E7F7-2021-11D2-93E0-0060B067B86E}
ITfKeyEventSink                  {AA80E7F5-2021-11D2-93E0-0060B067B86E}
ITfThreadMgrEventSink            {AA80E80E-2021-11D2-93E0-0060B067B86E}
ITfContextOwnerCompositionSink   {5F20AA40-B57A-4F34-96AB-3576F377CC79}
ITfInputProcessorProfiles        {1F02B6C5-7842-4EE6-8A0B-9A24183A95CA}
```

`ITextStoreACP` is not in that IDL; its documented id is `{28888FE3-C2A0-483A-A3EA-8CB1CE51FF3E}`
and it is the next piece to implement.

### Milestone 6 - a working TSF host in Python, and the RPC wire format

`host_tip_harness.py` now implements a minimal `ITextStoreACP` (26 methods whose vtable order
was extracted from Wine's `textstor.idl` by `extract_textstore_acp.py`) with ctypes-built
vtables, and hosts the vendor service end to end:

```
[2.2] docmgr vtable=0x7FF8F02891A0 QI(ITfDocumentMgr) hr=0x00000000
[2.6] store self-test: QI hr=0x00000000 GetEndACP hr=0x00000000 end=0
[qi] QueryInterface({28888FE3-C2A0-483A-A3EA-8CB1CE51FF3D}) ... (TSF probing our store)
[3] CreateContext(store) hr=0x00000000
[4] SetFocus(docmgr) hr=0x00000000
[5] CoCreateInstance(TIP) hr=0x00000000
[6] Activate hr=0x00000000
[7] QI(ITfKeyEventSink) hr=0x80004002      <-- the service does not expose a key sink
```

Two root causes were found and fixed on the way:

* a COM object's **first field must be the vtable pointer**; handing TSF the vtable array itself
  makes it read a callback address as the vtable and fail-fast (`0xC0000409`).
* callback thunks must be kept alive, otherwise the vtable holds dangling pointers.

Push-to-talk is therefore **not** delivered through `ITfKeyEventSink`; synthesized Right Alt
inside the host process produced no voice activity either. The most likely missing step is
activating the input profile so the service considers itself the active IME
(`ITfInputProcessorProfileMgr` = `{71C6E74C-0F28-11D8-A82A-00065B84435C}`,
`CLSID_TF_InputProcessorProfiles` = `{33C53A50-F456-4884-B049-85FD643ECFED}`, profile
`{2B4D4B3A-4D4F-4C0A-8E66-7F771A2B9C10}` for langid `0x0804`).

**RPC wire format captured** (`run_with_hooks.py` now logs pipe reads/writes; evidence
`planb/evidence/pipe_traffic_activate.txt`):

```
[writefile] \\.\pipe\ObricIme\oime-server len=12  head=4f4d5045 0100 1b00 00000000
                                                  "OMPE"  v1   op   payload_len
[writefile] \\.\pipe\ObricIme\oime-server len=22  head=307d0000 01000000 0a000000 "python.exe"
[writefile] \\.\pipe\ObricIme\oime-server len=5   head=08f8071801            (protobuf-ish body)
[readfile]  \\.\pipe\ObricIme\oime-server len=16  head=10ecded7 0a000000 10000000
[writefile] \\.\pipe\ObricIme\oime-server-tsf-log len=2315  head=544c4f47 "TLOG" ...
```

So the main pipe is a framed RPC channel (`OMPE`, version 1, u16 op, u32 length), and the
`-tsf-log` pipe carries `TLOG` records (the core's own log stream - useful as a debug feed).

### Milestone 7 - profile activated, core log readable, keystrokes still not delivered

`host_tip_harness.py` now also activates the input profile for our own process only:

```
[6.5] ITfInputProcessorProfileMgr hr=0x00000000
[6.6] ActivateProfile(doubao, 0x0804, FORPROCESS) hr=0x00000000
[7]   QI(ITfKeyEventSink) hr=0x80004002          <-- still no key sink on the TIP object
```

`dwFlags = TF_IPPMF_FORPROCESS (0x10000000)` keeps this local: the user's system-wide IME
selection is untouched.

The `-tsf-log` pipe turned out to be a **readable log stream** from the core, which is the
observability we were missing (`decode_tlog.py` parses captures):

```
DllMain PROCESS_ATTACH
TSF_STATE tag=DllMain.PROCESS_ATTACH proc=python.exe
LangBar AddItem ok
OnFocusChanged(true) begin.
```

So the service *does* see our context and installs its UI plumbing. But after injecting
synthesized Right Alt, the core log contains **zero** key/voice entries - the keystroke never
reaches TSF. That is expected for a synthetic host: TSF only routes keys (and only installs
per-process key handling) when the host has a **real window with keyboard focus and a message
loop**, and the app must also expose `ITfMessagePump` to the thread manager.

Next step (bounded): give the harness a real hidden/offscreen Win32 window + message loop,
implement `ITfMessagePump` and advise it to the thread manager, then send keys to that window
and watch the TLOG stream for `voice_start`-style entries. If the TIP still exposes no key
sink, the fallback is to hook the core's key handler directly and call it.
