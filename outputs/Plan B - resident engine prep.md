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

### Milestone 8 - real window + message loop; the hotkey is not in the TSF path at all

The harness now creates a real (offscreen) window, focuses it, posts Right Alt into its own
message queue and pumps messages. The core reacts to the host (TLOG):

```
OnFocusChanged(true) begin.
UpdateCursorPos GetFocus failed.            <-- because our store returned no HWND
ReportEditFocusState caret pending, retry scheduled left=2
```

After `ITextStoreACP::GetWnd` was changed to return the real window handle, the host is a
properly focused TSF host - and yet:

* the core log still contains **zero** key/voice entries after posting Right Alt;
* hooking `SetWindowsHookExW/A`, `RegisterHotKey`, `UnhookWindowsHookEx` shows the core makes
  **0 calls** to any of them inside our host process.

Conclusion: the push-to-talk key is **not captured in the TSF/key-sink path**. It must live in
the IME's UI/shell layer (or the engine), which a synthetic host does not initialise - that is
also why the standalone server's `shell init` mattered earlier.

Two routes remain, in order of cost:
1. drive voice **directly over the RPC pipe** (`OMPE` framing) - we already capture frames and
   can identify the "start/stop voice" op by recording one real session, then replay it from a
   minimal client (no TSF host needed at all);
2. initialise the IME's UI/shell inside our host (bigger surface: status bar, candidate
   window, caret tracking) so the shell installs its own hotkey handling.

Given the evidence, route 1 is the cheaper and more robust target for the fork.

### Milestone 10 - profile activation can be injected, but that alone does not engage the IME

`activate_profile_inject.js` + `activate_profile_inject.py` inject this into any process:

```
CoCreateInstance(CLSID_TF_InputProcessorProfiles) -> ITfInputProcessorProfileMgr
ActivateProfile(TF_PROFILETYPE_INPUTPROCESSOR, 0x0804, {9D2B2E2B-...}, {2B4D4B3A-...},
                NULL, TF_IPPMF_FORPROCESS)
```

Result in a real host (Notepad):

```
[activate] CoCreateInstance hr=0x00000000
[activate] ActivateProfile   hr=0x00000000      <-- succeeds
rpc.dll loaded in notepad = False               <-- but the IME never engages
```

So process-wide profile activation alone does not make the vendor's text service load: the
language/profile also has to be the *active input language of the thread*, which the shell does
when the user really switches IMEs. Locally we cannot fake that without impersonating the
language-bar path (Win+Space cycling did not switch either - verified with SendInput plus a
keystroke to force lazy loading).

Combined with milestone 9 (the engine's voice pool is pre-warmed at startup), the practical
conclusion for the fork is unchanged:

* the **engine side is fully understood and controllable** (private-identity resident server,
  warm voice pool, RPC framing captured);
* the **client side** needs either one captured real voice session (to replay the op codes) or
  the IME's own UI/shell, which engages only in a genuinely focused text host with the IME
  selected as the active language profile.

### Milestone 11 - a real EDIT control makes the IME accept our host; the receiving half works

Adding a genuine Win32 `EDIT` control to the host window (instead of a bare window) changed
everything on the receiving side. The core now treats us as a real text host and runs its voice
polling loop (TLOG):

```
ReportEditFocusState editable=1 has_ctx=1 readonly=...
QueryServerVoiceState state=0x0
PeekVoiceCommitText len=0 session=0
PullCommitText len=0
DrainServerDirtyState has_commit=0 commit_len=0
```

So **the text pickup path is live inside our host**: as soon as the engine has a voice session
with committed text, `PeekVoiceCommitText` returns it and the core inserts it into our text
store - exactly the half of Plan B we need for output.

What is still missing is the *trigger*: `state` stays `0x0` even when we inject a real Right
Alt (SendInput, host window focused). Module inventory of the host process shows
`tsf-oime.dll`, `tsf-oime-core.dll`, `rpc.dll` - but **no `ui.dll`**, so the push-to-talk key
lives in the IME's UI layer, which a hosted core does not load.

Two ways forward, both small:

1. capture one real voice session's RPC frames (needs the user to press Right Alt once in a
   real app window while `capture_pipe_attached.py` is attached) - then we know the op codes
   and can start the session from our own client;
2. find what makes the core load `ui.dll` (candidate window / status bar creation path) and
   host that too - bigger, more fragile.

### Milestone 12 - the missing ingredient is an *active* IME instance, not more hosting

Two measurements closed the loop:

1. **Module sets match exactly.** Real apps that have the IME loaded (ChatGPT, WeChat,
   explorer, SearchHost) contain `tsf-oime.dll`, `tsf-oime-core.dll`, `rpc.dll` - and, like our
   harness, **no `ui.dll`**. So our synthetic host is not missing a module that real apps have.
2. **The IME is loaded but not active anywhere right now.** Attaching
   `capture_pipe_attached.py` to ChatGPT.exe for 12 s showed only that app's own `uv` pipes -
   zero traffic on `\\.\pipe\ObricIme\oime-server`. A loaded but unselected text service
   produces no pipe traffic, which is why no keystroke we inject is ever answered.

Also, re-querying `ITfKeyEventSink` on the TIP *after* the host settled still returns
`E_NOINTERFACE` (hr=0x80004002), so the key handling really is not on the object we hold.

Conclusion for the fork: everything reachable locally is now mapped. The one remaining input
needed from a human is a **single real voice session with the IME actually selected**
(Win+Space until the input indicator shows Doubao, then hold Right Alt and speak). That capture
yields the op codes that start/stop voice; combined with milestone 11 (our host already picks
up committed text through `PeekVoiceCommitText`) that is enough to build the client.

### Milestone 13 - the whole pipe protocol is now readable (op table + wire formats)

Two tools replaced guesswork with ground truth:

* `planb/dump_ops.py` walks the dispatch jump table inside `rpc.dll`
  (`movzx eax, dx; dec eax; cmp eax, 0x1b; mov ecx,[rdx+rax*4+0x25688]; jmp rcx`) and lists
  all 28 op handlers; `planb/dump_proto.py` decodes the `server.proto` FileDescriptorProto
  embedded in `rpc.dll`, which names the messages (`KeyCode`, `CursorPos`, `CompTextType`, ...).
* `planb/oracle_client.py` loads the vendor's own `rpc.dll`, hooks the single function that
  frames every request (`rpc.dll+0x16650`) and calls the exported `RpcPipe_*` wrappers, so the
  *exact* op and body each call produces is captured instead of inferred.

Result: `planb/op_map.md`. Highlights - `0x04/0x05` KeyDown/KeyUp take a protobuf `KeyCode`;
`0x14` UpdateHostContext takes `(len+text)*3, u64 tick, (len+text)*2` (before/after/selected
text, reason, app); `0x17` PeekVoiceCommit / `0x18` AckVoiceCommit are the text pickup pair;
the handler for op *n* is vtable entry *n* in `ImeService.exe` (vtable at `+0x1009E78`).

### Milestone 14 - we can create a valid host context from a plain process

`planb/pipe_client.py` speaks the wire protocol over the private pipe
(`\\.\pipe\ObricIme\oime-serve1`) without loading any vendor DLL. Sending `KeyDown` alone left
the engine logging

```
[context][controller] apply-before-input key=165 no-valid-context
```

After sending `UpdateHostContext` (op 0x14, with the same field layout the oracle showed) plus
`FocusIn` and `RegisterTsfNotifySink`, the controller accepts the same key press - the
`no-valid-context` line is gone and `apply-before-input`/`cache-context` run normally. A plain
user-level process can therefore register itself as a *host* for the engine.

Note on the earlier "private" identity: `oime-server` -> `oime-serveR` only changes case, and
Windows resolves pipe/mutex names case-insensitively, so that copy collided with the installed
IME. `patch_names.py` now uses a digit (`oime-serve1`), and `--from-orig` re-applies the whole
chain (restore -> name -> manifest -> voice hook).

### Milestone 15 - the voice hotkey is inside the engine, and it is guarded twice

The voice key never travels over the pipe: `ImeService.exe` installs its own
`WH_KEYBOARD_LL` hook (`server_main.cpp:370 voice key hook installed`) whose callback is
`VoiceKeyHookProc` (`ImeService.exe+0x7426C0`). Two guards stop a client from triggering it:

1. **Injected input is dropped** (`test byte [lParam+8], 0x10; jne bypass` at
   `+0x742743`) - `keybd_event`/`SendInput` never reaches the voice logic.
2. **A state gate** in front of the handler (`+0x74270C..+0x742734`) that consults a global
   state object: the hook only takes the voice path when the *settings UI* state allows it;
   otherwise it logs `[VHK] bypass all keys: settings ui focused, voice tryout inactive`.
   The same binary contains the settings IPC commands that flip that state
   (`ime::settings::SettingsIpcServer::HandleSetVoiceTryoutActive`,
   `settings.startShortcutRecording`, `[settings-ipc] startMicMeter ...`).

`planb/patch_voicehook.py` NOPs both guards in our private copy (only our copy), which makes
the hook consume an injected Right Alt and run its state machine:
`[VHK][Key] passthrough state sync vk=0xA5 down=1 up=0 down_cn=1`. It still ends in the
"bypass all keys" branch, so the missing piece is now precisely the settings-IPC side: the
engine expects `setVoiceTryoutActive(true)` (from `DoubaoImeSettings.exe`, via
`DoubaoIme.Settings.NativeRuntime.dll` -> same pipe) before the hotkey starts a session.

### Milestone 16 - what is left, and the two ways to finish it locally

Everything except the voice *trigger* is now proven with our own user-level code: private
engine instance, host context, key handling, and the `PeekVoiceCommit`/`AckVoiceCommit` text
pair (`[controller.cpp:2462] slot_PeekVoiceCommit session=0 bytes=0 acked=1` answers every
poll). Two concrete triggers remain to be closed, both local:

1. **Settings-IPC route** - patch `DoubaoIme.Settings.NativeRuntime.dll` to the private pipe,
   launch the settings UI, and let its voice page call `setVoiceTryoutActive(true)`; then the
   hook path becomes active and (with the injected-key patch) our client can drive it.
2. **Call the in-engine start path** - find the message `VoiceKeyHookProc` posts to the main
   thread (`[VHK][proc] PostToMainThread msg = %d, wp = %d, lp=%d`) and post it from our
   client, or call the `Controller` voice-start entry directly.

Either way the output half is already settled: `PeekVoiceCommit` on the private pipe is the
text channel, and it is exactly what the v0.1.0 product would poll instead of the file-based
`--test-sami` path.

### Milestone 17 - the settings channel is captured and replayable

`capture_settings_ipc.py` spawns `DoubaoImeSettings.exe` under frida with pipe hooks and
captured the channel the voice state rides on:

```
\\.\pipe\DoubaoIme\settings-rpc
u32 length | {"version":1,"requestId":"<32 hex>","method":"settings.get"}
u32 length | {"version":1,"ok":true,"payload":{...},"requestId":"..."}
```

Methods seen: `settings.get`, `settings.getMicrophoneList`, `settings.getRuntimeStatus`,
`settings.update`, `settings.validateShortcut`, `settings.startShortcutRecording`,
`settings.stopShortcutRecording`, `settings.setVoiceTryoutActive`, `settings.startMicMeter`,
`settings.stopMicMeter`, `settings.getMicLevel`, `settings.completeOnboarding`.

`settings_ipc_client.py` speaks that framing; against the running IME it returns
`{"ok": true, "payload": {"systemChEnHotkeyIsCtrlSpace": true}, ...}` (evidence in
`planb/evidence/settings_ipc_response.txt`), so a plain user-level process can set the
voice-tryout state - which is the last gate in front of the engine's voice session. Our copy
already renames its settings pipe (`settings-rp1`) so it never fights the installed IME for it.

### Milestone 18 - the two gates in front of the voice hotkey are both open now

Two details were all that separated "the hook sees my key" from "the engine starts recording":

1. **The real settings pipe is a wide string.** `ImeService.exe` stores
   `\\.\pipe\DoubaoIme\settings-rpc` as UTF-16; the ASCII copy is only the log message. After
   adding the UTF-16 pair to `patch_names.py`, our copy owns `settings-rp1` and
   `settings.setVoiceTryoutActive(true)` reaches *our* engine
   (`[settings-ipc] setVoiceTryoutActive active=1`).
2. **`FocusIn` carries the requester's pid.** `rpc.dll` stores the first u32 of the FocusIn
   body as the "focus owner" (`[r13+0x84]`, global `0x1802FD154`) and the next u32 as its
   caps. Our first attempts replayed the harness capture verbatim, which still contained the
   *harness's* pid, so the engine thought someone else was focused and kept answering
   `[... ] ACTIVATION but not allowed (ime not foreground-active)`. Sending our own pid turns
   that line into `allowed=1`.

The host window also has to genuinely own the foreground: plain `SetForegroundWindow` is
ignored unless the caller is the foreground process, so `make_foreground_window()` borrows the
current foreground thread's input queue (`AttachThreadInput`) and then calls
`SetForegroundWindow`/`SetWindowPos`. Loading the vendor TIP into our process
(`tsf_activate.py`) keeps the app looking like a normal IME client.

### Milestone 19 - the engine now records and streams on command (end to end, user level)

`planb/voice_session_test.py` runs the whole chain against the private copy:

```
[arm]   settings.setVoiceTryoutActive(True)            -> {"ok": true}
[host]  window 0x... foreground, Doubao profile active for this process
[ctx]   Activate / ImeChanged / SetUIElementShowState / FocusIn(pid) /
        RegisterTsfNotifySink(hwnd) / UpdateHostContext        -> all status 0
[key]   injected Right Alt down (no keyboard touches anything)
```

Engine side (evidence: `planb/evidence/voice_session_triggered.txt`):

```
[VHK][Key]      vk=0xA5(165) down=1 up=0 allowed=1 wParam=0x0104
[VHK][Trig]     ACTIVATION long press
[VHK][Trig]     ACTIVATION long --- post show wave 0
[MicList]       enumerated 1 capture device(s), default_id={0.0.1.00000000}.{...}
[MicPick]       resolved endpoint_id to waveIn index=0
[sami_asr]      asr session started business=oime_windows task_id=ime_win_... sample_rate=16000
[controller]    voice record start ok=1 asr_ok=1 path=
[controller]    voice startfrom reported reason=shortcut start_method=long_click
[sami_asr]      sami feed chunk bytes=1280        (continuous, real time)
```

That is the missing trigger: **our own client can start the engine's own push-to-talk voice
session, which captures the microphone and streams to the vendor cloud ASR**, with no TSF
registration, no admin rights and no file-based `--test-sami` detour.

What is still open is only the *return* path: a raw pipe client polling `PeekVoiceCommit`
gets `session=0 bytes=0`, because the transcript is handed to the **TSF notify sink / core**
that lives in the host process (the engine posts to the registered hwnd). Milestone 11 already
proved the receiving half - a hosted text store receives the committed text through the core -
so the next step is to run the text-store host in the same process as this trigger, and the
loop is closed.

### Milestone 20 - no focus stealing, and where the transcript actually goes

Two more measurements narrow the remaining work:

1. **The "focus owner" must simply be the current foreground pid** - not our own pid. Sending
   `FocusIn(pid = <foreground window's pid>)` from a background process is enough to get
   `allowed=1`, so a product does not have to steal focus from the user's editor to start a
   session. `voice_session_test.py --foreign-focus` demonstrates that (no window created at
   all, hotkey still accepted).
2. **The transcript is pushed to the registered notify sink, not pulled.** With the sink hwnd
   registered to a window we own, the engine posts to it while dictating
   (`id=0x031F wparam=1`, `id=0xC109`); `PeekVoiceCommit` stays `session=0 bytes=0` for a raw
   pipe client even when the same session is streaming audio to the cloud. So the return path
   is either (a) pump those notifications and decode the payload, or (b) host the text store
   from milestone 11 - which already receives the committed text through the core - in the
   same process that runs this trigger.

Current known rough edge: our message pump crashes (access violation) shortly after those two
notifications arrive, so decoding the payload is the next concrete task; the engine side keeps
running normally and the audio keeps streaming.

### Milestone 21 - the engine does recognize our audio; the stop is what blocks the commit

Three measurements this round, all with the private copy:

1. **The cloud ASR returns real text for a session we triggered.** With the test wav played
   through the speakers, the engine log contains
   `payload: {"results":[{"is_interim":true,"text":"..."}]}` whose text grows with the
   utterance (`X` is the engine's own placeholder for non-ASCII in its log, but a full-width
   comma survives: `XAXX，XX`). Saved as `planb/evidence/asr_results.txt`;
   `planb/decode_asr_text.py` extracts the fields.
2. **A non-empty host context is accepted.** Sending `UpdateHostContext` with before/after text
   (instead of empty strings) makes the engine cache it -
   `[context][shell] apply-context ... has_context=1` - and it then feeds that text to the ASR
   as recognition context (`[asr-context] ... cur_before_len=6 cur_after_len=6`).
3. **RPC key events do not start voice.** `KeyDown`/`KeyUp` (op 0x04/0x05, `KeyCode`) are
   accepted (status 0) but the engine never begins a session; the low-level hook really is the
   only trigger - `planb/voice_session_rpc.py` shows no `voice startfrom` at all.

That leaves one precise blocker for the transcript: the session never *commits* because it
never stops cleanly. Instrumenting the hook (`planb/hook_peek.py`, which also hooks
`VoiceKeyHookProc`) shows the mechanism:

```
[VHK][Trig] combo Alt 0xA5 UP -> real UP eaten + synthesized release (suppress menu)
[VHK][Trig] SynthesizeMetaRelease meta=0xA5 is_win=0 events=3 injected
```

The engine deliberately swallows the real key-up and injects its own synthetic release events
(seen as `vk=0xFC`, flags `0x10`/`0x90`). Our copy's patch removed the *injected* filter so
that our scripted key would be accepted - which also makes the engine see **its own**
synthetic events, and it then logs `unrelated key DOWN while consumed, cancel`: the session is
cancelled instead of finalized, so `PeekVoiceCommit` never has committed text
(`session=0 bytes=0`, 970/970 polls in the last run).

The fix is to narrow the patch: keep ignoring *engine-synthesized* injected keys and accept only
ours. The two candidates to discriminate on are the scan code (our injections use 0) and
`dwExtraInfo` (both currently read 0, so the synthesized events likely carry a magic or a
distinct vk such as `0xFC`). Once the stop finalizes cleanly, the commit - and therefore the
transcript - should appear on the existing `PeekVoiceCommit` path, which our raw client already
reaches (the engine logs `slot_PeekVoiceCommit` for every one of our polls).

### Milestone 22 - the stop exists (`PRESS_STOP`), the final ASR result arrives, but the client
still gets nothing

Two more facts, both from `planb/hook_peek.py` / `planb/hook_synth.py`:

1. **An unrelated key press is a clean stop.** While a session records, pressing any other key
   makes the hook post `VOICE_PRESS_STOP` and the controller logs
   `[RAlt] controller handle msg=PRESS_STOP recording=1 want_start=0 want_stop=1` (evidence:
   `planb/evidence/stop_and_final_result.txt`). That is a documented, reachable stop path that
   does not depend on our synthetic Alt-up.
2. **The engine does receive a final result.** With a ~7 s hold and the stop above, the log
   contains `{"is_interim":false,"text":"..."}` - the cloud's final sentence - so capture,
   streaming, endpointing and finalisation all happen.

What is still missing is the *hand-off to a client*: with the stop happening, `PeekVoiceCommit`
still answers `session=0 bytes=0` (679 polls in that run), and the engine posts nothing to the
registered notify sink hwnd (`planb/hook_sink.py` hooked `PostMessageW`, `SendMessageW` and
`PostThreadMessageW` inside the engine and saw no notification during the session). The engine
only serves a transcript to a client it considers a real text service - which is exactly what
milestone 11 built: a hosted TSF text store in the same process. The remaining work is
therefore to run that host *against the private engine* (load the copy's patched
`tsf-oime-core.dll` by path via `DllGetClassObject` instead of the COM-registered installed
one), so the core is the client that receives the commit text, and we read it out of the store.
