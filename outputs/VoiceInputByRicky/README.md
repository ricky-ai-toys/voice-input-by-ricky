# Voice Input by Ricky - portable voice typing (no admin)

**Hold `Right Alt`, speak, release - the words are typed at your cursor.**
Nothing is installed: no service, no driver, no input-method (TSF) registration, no
administrator rights, no changes to Windows settings.

[English](#english) | [中文](#中文)

---

## English

### Quick start

1. Copy this whole folder anywhere - Desktop, `D:\Tools`, a USB stick.
2. Double-click **`VoiceInputByRicky.exe`** (or the `Voice Input by Ricky.cmd` launcher).
   Windows does **not** ask for a password (no UAC prompt).
3. A badge appears: `Ready - hold the hotkey and speak`.
4. In any text box (browser, chat, Word, terminal...): **hold Right Alt -> speak -> release.**
   - `Listening...` while you speak, with the recognized words streaming into the badge;
   - the finished sentence is pasted at the cursor a moment after you release.
5. To quit: close the console window that the app keeps open.

On the first run the app creates `data\config.json` (settings).

### Languages

The engine detects the language by itself - there is nothing to configure. Mandarin, Cantonese
and English can be mixed inside one utterance.

### Speed

Measured on this build with a 7 s utterance: the first words appear **while you are still
speaking**, and the final sentence is inserted **0.6-1.2 s after you release** (the engine
finalises the sentence, then the shell pastes it). Acknowledging each finished sentence is what
keeps the *next* press instant.

### Layout

```
VoiceInputByRicky\
  VoiceInputByRicky.exe      the shell (hold the hotkey -> the engine's transcript is pasted)
  runtime\                   copy of the Doubao IME speech engine, with private pipe names and
                             uiAccess="false" so it runs as an ordinary user process
  data\config.json           settings, created on first run
  data\logs\                 one log per run (app-<date>.log, engine-<pid>.log)
  Voice Input by Ricky.cmd   launcher (optional)
  README.md                  this file
```

The folder can be moved to another PC or a USB stick as-is.

### Requirements

| Item | Requirement |
| --- | --- |
| Windows | Windows 10 / 11, 64-bit |
| Rights | standard user - **no administrator rights at any point** |
| Audio | one microphone; the engine records at 16 kHz mono itself |
| Network | access to `*.doubao.com` (recognition runs in the vendor's cloud) |
| Disk | about 260 MB |

### Settings - `data\config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `"right alt"` | push-to-talk key: `right alt`, `left alt`, `right ctrl`, `left ctrl`, `right shift`, `left shift`, `caps lock`, `left win`, `right win` |
| `show_overlay` | `true` | show the status badge while dictating |
| `paste` | `true` | insert the text (set to `false` to only log it) |
| `commit_timeout` | `2.5` | seconds to wait for the final sentence after you release |
| `microphone` | `""` | endpoint id to record from; empty means "the Windows default device". List the ids with `VoiceInputByRicky.exe --list-mics` |

Edit the file, then restart the app.

### Microphone

The engine records from the device named in `microphone`, or from the Windows default recording
device when that is empty. At every start the app checks that the device still exists and is
active, fills the engine's own setting in when it is missing, and writes what it found to the log.
If dictation returns `No speech detected`, look at **Settings > System > Sound > Input**: the
device may be muted, unplugged, or simply not the one you are speaking into.

```
VoiceInputByRicky.exe --list-mics                     # show the ids
VoiceInputByRicky.exe --microphone "<endpoint id>"    # run with another device
```

### Logs

Every run appends to `data\logs\app-<date>.log`, and the engine's own output goes to
`data\logs\engine-<pid>.log`. Each dictation writes a few lines there: the window that had focus
when you pressed, what the engine decided (`voice record start ok=1`, `blocked`, `not allowed ...`),
the speech-recognition counters, and the text that was inserted. Those two files are what to send
when something does not work.

### Troubleshooting

| Symptom | What to check |
| --- | --- |
| No badge after the double-click | the console window's last lines say why; make sure the whole folder was copied (including `runtime\`) |
| Badge appears but nothing is typed | is the target app focused when you release? windows running as administrator refuse input from a normal-privilege app |
| `No speech detected - check the microphone` | the engine recorded, but the cloud heard silence - check the input device and its level |
| `Engine did not start recording - see the log` | the engine never started a session; `data\logs\engine-*.log` says why |
| `Engine refused to record (busy) - try again` | a previous sentence was not acknowledged; the app clears that at startup, so this should be transient |
| `Nothing recognised` | this PC cannot reach `*.doubao.com`, or the speech was unclear |
| Nothing works any more | close the app and start it again: on startup it stops leftover `ImeService.exe` processes from this folder and clears a stale acknowledgement |

Only one copy of the app can run at a time; a second double-click shows a message instead of
fighting over the engine.

### Known limitations

* Recognition needs a network connection; nothing is cached locally.
* The channel is a **private vendor interface**: a future Doubao IME update can break this
  build. The bundled engine copy is pinned to Doubao IME v0.9.0.0.
* Languages are auto-detected; there is no manual language switch.
* Windows opened as administrator will not accept the pasted text (Windows security rule).

### Uninstall

1. Close the app's console window and end any `ImeService.exe` started from this folder.
2. Delete the folder.
3. The engine's own cache under `%APPDATA%\DoubaoIme` (logs and dictionaries) can be deleted too.

No registry keys, services, scheduled tasks or input-method registrations are created.

### How it works

`runtime\ImeService.exe` is the speech engine that ships with Doubao IME. The shell starts it as
an ordinary child process and talks to its private local pipe:

* it opens the voice hotkey gate and registers itself as the host that owns the foreground window;
* it watches the real hotkey locally (the key is never injected, so the engine sees a genuine
  key press) and polls `GetInlineCommitText` for the streaming transcript;
* after the key is released it reads the final sentence through `RpcPipe_PeekVoiceCommitUtf8`,
  pastes it, and calls `RpcPipe_AckVoiceCommit` - the acknowledgement that lets the next
  dictation start immediately.

### Compliance

This is a personal interoperability project. It bundles a **copy** of the speech engine that
ships with Doubao IME (v0.9.0.0), talks to the vendor's own cloud service, and relies on
undocumented private interfaces - the vendor's terms may not allow that, and it is intended
for your own machine only. Audio is uploaded to Doubao's cloud, so check your employer's
rules before using it on a work device. **Do not redistribute the bundled engine files.**

---

## 中文

**按住 `右 Alt` 说话，松开后文字自动落在光标处。**
不安装任何东西：没有服务、没有驱动、不注册输入法（TSF）、**全程不需要管理员权限**，也不改
Windows 的任何设置。

### 快速开始

1. 把整个文件夹拷到任意位置——桌面、`D:\Tools`、U 盘都行。
2. 双击 **`VoiceInputByRicky.exe`**（或 `Voice Input by Ricky.cmd`）。**不会弹 UAC**。
3. 屏幕上出现提示条：`Ready - hold the hotkey and speak`。
4. 在任意输入框（浏览器、聊天、Word、终端……）：**按住右 Alt → 说话 → 松开**。
   - 说话时提示条显示 `Listening...`，识别出的文字实时流出；
   - 松开后约 0.6-1.2 秒，整句文字自动粘贴到光标处。
5. 退出：关掉程序保持打开的那个控制台窗口。

第一次运行会在本文件夹内创建 `data\config.json`（配置）。

### 语言

引擎自己判断语种，无需任何配置；普通话、粤语、英文混在一句话里也能正确出字。

### 速度

本版本实测（7 秒语音）：**说完之前就已经开始出字**，整句在**松手后 0.6-1.2 秒**落地。
每句话结束都会向引擎确认（ack），这正是"第二次按键立刻可用"的原因。

### 目录结构

```
VoiceInputByRicky\
  VoiceInputByRicky.exe      外壳程序（按住热键 → 把引擎的识别结果粘贴出来）
  runtime\                   豆包输入法语音引擎的副本：管道名私有化 + uiAccess="false"，
                             因此能以普通用户身份运行
  data\config.json           配置，首次运行自动生成
  data\logs\                 每次运行的日志（app-<日期>.log、engine-<pid>.log）
  Voice Input by Ricky.cmd   启动器（可选）
  README.md                  本文件
```

整个文件夹可以直接搬到别的电脑或 U 盘上使用。

### 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 64 位 |
| 权限 | 普通用户即可，**全程不需要管理员权限** |
| 音频 | 一个麦克风，由引擎自己以 16 kHz 单声道录音 |
| 网络 | 能访问 `*.doubao.com`（识别在厂商云端完成） |
| 磁盘 | 约 260 MB |

### 设置（`data\config.json`）

| 键 | 默认值 | 含义 |
| --- | --- | --- |
| `hotkey` | `"right alt"` | 按住说话键：`right alt`、`left alt`、`right ctrl`、`left ctrl`、`right shift`、`left shift`、`caps lock`、`left win`、`right win` |
| `show_overlay` | `true` | 说话时显示提示条 |
| `paste` | `true` | 是否粘贴（设为 `false` 则只打印不粘贴） |
| `commit_timeout` | `2.5` | 松手后等待整句结果的最长秒数 |
| `microphone` | `""` | 指定录音设备 ID；留空表示跟随 Windows 默认录音设备。用 `VoiceInputByRicky.exe --list-mics` 查看可用 ID |

改完配置后重启程序生效。

### 麦克风

引擎从 `microphone` 指定的设备录音；留空则使用 Windows 默认录音设备。程序每次启动都会检查该
设备是否仍然存在且处于活动状态，缺失时自动写入引擎自己的设置，并把检查结果写进日志。
如果提示 `No speech detected`，请到 **设置 → 系统 → 声音 → 输入** 检查：设备可能被静音、被拔出，
或者根本不是你正在说话的那一个。

```
VoiceInputByRicky.exe --list-mics                     # 列出设备 ID
VoiceInputByRicky.exe --microphone "<设备 ID>"         # 用别的设备跑一次
```

### 日志

每次运行都会追加写入 `data\logs\app-<日期>.log`，引擎自己的输出在 `data\logs\engine-<pid>.log`。
每次口述会留下几行记录：按下时前台窗口是哪个、引擎做了什么决定（`voice record start ok=1`、
`blocked`、`not allowed ...`）、云端识别计数，以及最终插入的文字。**出问题时把这两个文件发我即可。**

### 常见问题

| 现象 | 排查 |
| --- | --- |
| 双击后没有提示条 | 看控制台窗口最后几行；确认整个文件夹都复制完整（包含 `runtime\`） |
| 有提示条但不出字 | 松手时目标窗口是否处于焦点？以管理员身份运行的窗口会拒绝普通权限程序注入的文字 |
| 显示 `No speech detected - check the microphone` | 引擎已经在录音，但云端收到的是静音——检查输入设备和音量 |
| 显示 `Engine did not start recording - see the log` | 引擎没有开始会话，`data\logs\engine-*.log` 会写明原因 |
| 显示 `Engine refused to record (busy) - try again` | 上一句还没被确认；程序启动时会自动清理，通常只是偶发 |
| 显示 `Nothing recognised` | 本机无法访问 `*.doubao.com`，或说话不清楚 |
| 突然完全不能用 | 关掉程序再重新启动：启动时会自动结束**本文件夹**残留的 `ImeService.exe` 并清掉陈旧的确认状态 |

程序同一时间只允许运行一个实例；重复双击会给出提示，而不是两个实例抢同一个引擎。

### 已知限制

* 识别需要联网，本地不缓存任何识别结果。
* 走的是厂商**私有接口**：豆包输入法将来更新可能让本版本失效。内置引擎副本固定在
  豆包输入法 v0.9.0.0。
* 语种自动识别，没有手动切换开关。
* 以管理员身份运行的窗口不接受粘贴（Windows 安全限制）。

### 卸载

1. 关闭程序的控制台窗口，并结束所有从本文件夹启动的 `ImeService.exe`。
2. 删除整个文件夹。
3. 可选：删除引擎自己的缓存目录 `%APPDATA%\DoubaoIme`（日志与词库）。

不会写入注册表、服务、计划任务，也不注册输入法。

### 工作原理

`runtime\ImeService.exe` 就是豆包输入法自带的语音引擎。外壳程序把它作为普通子进程启动，
然后只通过它的本地私有管道通信：

* 打开语音热键开关，并把"拥有前台窗口的宿主"登记为自己；
* 在本地监听**真实的**热键（从不注入按键，所以引擎看到的是真实按键），同时轮询
  `GetInlineCommitText` 拿流式识别文字；
* 松手后通过 `RpcPipe_PeekVoiceCommitUtf8` 取整句结果，粘贴出来，再调用
  `RpcPipe_AckVoiceCommit` 确认——这一句 ack 就是"下一次按键立刻可用"的关键。

### 合规提醒

本项目属于个人互操作性质：打包了本机安装的豆包输入法（v0.9.0.0）语音引擎的**副本**，调用
厂商自己的云服务，并依赖未公开的私有接口——厂商条款可能不允许这种做法，仅供你本机自用。
音频会上传到豆包云端，在公司设备上使用前请确认符合公司规定。**请勿再分发打包的引擎文件。**
