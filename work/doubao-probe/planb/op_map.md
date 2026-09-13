# Wire ops of the Doubao engine pipe (recovered 2026-09-13)

Frame layout (confirmed against `rpc.dll` both statically and on the wire):

```
request : "OMPE" | u16 version(1) | u16 op | u32 body_len | body
response: "OMPE" | u16 version(1) | u16 op | u32 status  | u32 body_len | body
```

Bodies are either protobuf messages (see `dump_proto.py`) or a run of
`u32 length + bytes` fields (`PipeServer` style), depending on the op.

`op` is also the index into the service implementation's vtable in `ImeService.exe`
(`vtable = 0x140000000 + 0x1009E78`, `entry[op]` = handler).

| op | name (from exports / descriptors / logs) | impl RVA | body |
|----|------------------------------------------|----------|------|
| 0x01 | SendHeart | 0x810030 | - |
| 0x02 | Activate | 0x80f4e0 | - |
| 0x03 | DeActivate | 0x80f5c0 | u32 field? (`04380000`) |
| 0x04 | KeyDown (`RpcPipe_KeyDown`) | 0x80fb30 | protobuf `KeyCode{int32 keycode=1, uint64 perfstart=2, string process_name=3}` |
| 0x05 | KeyUp (`RpcPipe_KeyUp`) | 0x80fd70 | `KeyCode` |
| 0x06 | FocusIn (`RpcPipe_FocusIn`) | 0x80f620 | `u32 tag | u32 count | (u32 len + bytes)*` — the core sends the host process name |
| 0x07 | FocusOut | 0x80f6a0 | - |
| 0x08 | SetCursorPos (`RpcPipe_SetCursorPos`) | 0x80f730 | `CursorPos` |
| 0x09 | GetCompText (`RpcPipe_GetCompTextUtf8`) | 0x810040 | -> `CompTextType` |
| 0x0A | GetCommitText (`RpcPipe_GetCommitTextUtf8`) | 0x8103b0 | -> `StringType` |
| 0x0B | GetCandidateList (candidate window off) | 0x80f8c0 | - |
| 0x0C | SimpleMessage / SimpleMessageEx | 0x80f830 | `SimpleMsgType` (id, wparam, lparam, process) |
| 0x0D | GetInputState (`RpcPipe_GetInputState`) | 0x80f780 | `Int32Type` -> `BoolType` |
| 0x0E | SetInputState | 0x810140 | `InputState` |
| 0x0F | ConfigChanged | 0x8101b0 | `ConfigType` |
| 0x10 | LogEvent | 0x80f960 | `ReportEventType` |
| 0x11 | ImeChanged | 0x80fed0 | `ImeChangedType` |
| 0x12 | KeyEvent (`RpcPipe_KeyEvent`) | 0x80f450 | rich key event (length-prefixed blocks) |
| 0x13 | UploadModifyPair (`RpcPipe_UploadModifyPair`) | 0x80ffd0 | json |
| 0x14 | UpdateHostContext (`RpcPipe_UpdateHostContextUtf8`) | 0x810280 | `(u32 len+bytes)*3, u64, (u32 len+bytes)*2` = before/after/selected text, tick, reason, app |
| 0x15 | RegisterTsfNotifySink (`RpcPipe_RegisterTsfNotifySink`) | 0x80f9f0 | 16 bytes (hwnd + cookie) |
| 0x16 | (unmapped, parses a body) | 0x8100d0 | - |
| 0x17 | PeekVoiceCommit (`RpcPipe_PeekVoiceCommitUtf8`) | 0x80f530 | -> 12 bytes (session + text) |
| 0x18 | AckVoiceCommit (`RpcPipe_AckVoiceCommit`) | 0x80fe00 | session id |
| 0x19 | GetInlineCommitText (`RpcPipe_GetInlineCommitTextUtf8`) | 0x80fa70 | -> text |
| 0x1A | GetCandidateList (`RpcPipe_GetCandidateListUtf8`) | 0x8104b0 | -> `CandidateListType` |
| 0x1B | SetUIElementShowState (`RpcPipe_SetUIElementShowState`) | 0x80ff60 | `Int32Type` |
| 0x1C | ShowContextMenu (`RpcPipe_ShowContextMenu`) | 0x810340 | `ContextMenuRequest` |

Evidence: `oracle_client.py` (calls the vendor's own exports and captures the framed request),
`dump_ops.py` (reads the dispatch jump table), `dump_proto.py` (decodes the embedded
`server.proto` descriptor), `hook_drive.py` (hooks the dispatcher and prints op -> impl).
