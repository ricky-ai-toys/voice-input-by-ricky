# Voice Input by Ricky - portable voice typing (no admin)

**Hold `Right Alt`, speak, release - the words are typed at your cursor.**
Nothing is installed: no service, no driver, no input-method (TSF) registration, no
administrator rights, no changes to Windows settings.

[English](#english) | [中文](#中文)

---

## English

### Quick start

1. Copy this whole folder anywhere - Desktop, `D:\Tools`, a USB stick.
2. Double-click **`Voice Input by Ricky.cmd`**. Windows does **not** ask for a password
   (no UAC prompt).
3. A badge appears: `Ready - hold Right Alt and speak`.
4. In any text box (browser, chat, Word, terminal...): **hold Right Alt -> speak -> release.**
   - `● Listening...` while you speak, with the recognized words streaming into the badge;
   - `... Transcribing` after you release;
   - the finished text is pasted at the cursor.
5. To quit: end **`VoiceInputByRicky.exe`** in Task Manager - the app is resident and has no
   window of its own.

On the first run the app creates `data\config.json` (settings) and `data\logs\app.log`.
Everything it writes stays inside this folder.

### Languages

The engine detects the language by itself - there is nothing to configure. Verified with this
build:

| Language | Result |
| --- | --- |
| Mandarin | `我确认程序能用，而且不错。请给出下一步的升级迭代规划。` |
| Cantonese speech | `请问听到吗？我讲广东话，一二三。` |
| English | `Good morning, everybody. Today is Sunday. I'm going to go swimming with my daughter...` |

Mandarin, Cantonese and English can be mixed inside one utterance.

### Speed

Measured on a ~4 s utterance with this build: text starts appearing **~1.5 s after you start
speaking** and the final text lands **~1.1-1.7 s after you release**. One recognition session
is kept pre-connected in the background, so the first press is not the slow one.

### Layout

```
VoiceInputByRicky\
  app\VoiceInputByRicky.exe      the app (bundled Python runtime, frida, audio libs)
  runtime\v0.9.0.0\              copy of the Doubao IME speech engine, with one manifest
                                 attribute rewritten so it runs as a standard user
  data\                          config.json, logs\app.log, live_*.wav scratch files
  Voice Input by Ricky.cmd       launcher
  README.md                      this file
```

The folder can be moved to another PC or a USB stick as-is.

### Requirements

| Item | Requirement |
| --- | --- |
| Windows | Windows 10 / 11, 64-bit |
| Rights | standard user - **no administrator rights at any point** |
| Audio | one microphone; the engine works on 16 kHz mono |
| Network | access to `*.doubao.com` (recognition runs in the vendor's cloud) |
| Disk | about 300 MB |

### Settings - `data\config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `"right alt"` | push-to-talk key: `right alt`, `left alt`, `right ctrl`, `left ctrl`, `right shift`, `left shift`, `caps lock`, `left win`, `right win` |
| `suppress_hotkey` | `false` | `true` hides the key from other apps (Alt menus stop working - use with care) |
| `paste_delay_ms` | `60` | delay between focusing the target window and sending `Ctrl+V` |
| `restore_clipboard` | `true` | put your previous clipboard content back after pasting |
| `show_overlay` | `true` | show the status badge while dictating |
| `final_timeout_sec` | `8.0` | how long to wait for the final text after you release |
| `min_duration_sec` | `0.35` | presses shorter than this are ignored |

Edit the file, then restart the app.

### Start with Windows (optional, still no admin)

```
app\VoiceInputByRicky.exe --install-autostart
app\VoiceInputByRicky.exe --remove-autostart
```

This writes one `.cmd` file into your own Startup folder
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`). Nothing machine-wide.

### Troubleshooting

| Symptom | What to check |
| --- | --- |
| No badge after the double-click | `data\logs\app.log` - the last lines say why; make sure the whole folder was extracted (not only the `.cmd`) |
| Badge appears but nothing is typed | is the target app focused when you release? Windows running as administrator refuse injected input from a normal-privilege app |
| `(nothing recognized)` | microphone level, and that this PC can reach `*.doubao.com` |
| Nothing works any more | end every `ImeService.exe` started from this folder, then start the app again (it also reaps its own leftovers on start) |
| Long dictation is cut short | see the limitation below - it is a known issue, not a broken install |

### Known limitations

* **Dictation longer than ~40 s is not reliable yet.** The interface this build drives is
  file-based: the engine reads a growing WAV, its read rate varies between 0.6x and 2.5x real
  time, and reaching the end of the file ends the session. The planned fix is to drive the
  engine as a resident server over its local pipe (Plan B), described in
  `Plan B - resident engine prep.md` in the repository.
* Recognition needs a network connection; nothing is cached locally.
* The channel is a **private vendor interface**: a future Doubao IME update can break this
  build. The bundled engine copy is pinned to Doubao IME v0.9.0.0.
* Languages are auto-detected; there is no manual language switch.

### Uninstall

1. End `VoiceInputByRicky.exe` (and any `ImeService.exe` started from this folder).
2. Delete the folder.
3. If you used `--install-autostart`, also delete
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DoubaoVoice.cmd`.

No registry keys, services, scheduled tasks or input-method registrations are created.

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
2. 双击 **`Voice Input by Ricky.cmd`**。**不会弹 UAC**，也不会要求管理员密码。
3. 屏幕上出现提示条：`Ready - hold Right Alt and speak`。
4. 在任意输入框（浏览器、聊天、Word、终端……）：**按住右 Alt → 说话 → 松开**。
   - 说话时显示 `● Listening...`，识别出的文字会实时显示在提示条上；
   - 松开后显示 `... Transcribing`；
   - 最终文字自动粘贴到光标处。
5. 退出：在任务管理器里结束 **`VoiceInputByRicky.exe`**（程序常驻，本身没有窗口）。

第一次运行时会在本文件夹内创建 `data\config.json`（配置）和 `data\logs\app.log`（日志），
所有写入都只在这个文件夹里。

### 语言

引擎自己判断语种，无需任何配置。本版本实测：

| 语言 | 实测输出 |
| --- | --- |
| 普通话 | `我确认程序能用，而且不错。请给出下一步的升级迭代规划。` |
| 粤语口述 | `请问听到吗？我讲广东话，一二三。` |
| 英文 | `Good morning, everybody. Today is Sunday. I'm going to go swimming with my daughter...` |

普通话、粤语、英文混在一句话里也能正确出字。

### 速度

本版本实测（约 4 秒语音）：**开始说话后约 1.5 秒出首字**，**松手后约 1.1-1.7 秒落地**。
程序在后台保持一条预热好的识别会话，所以第一次按键也不慢。

### 目录结构

```
VoiceInputByRicky\
  app\VoiceInputByRicky.exe      主程序（内置 Python 运行时、frida、音频库）
  runtime\v0.9.0.0\              豆包输入法语音引擎的副本，副本内改写了 1 个清单属性
                                 使其能以普通用户身份运行
  data\                          config.json、logs\app.log、live_*.wav 临时文件
  Voice Input by Ricky.cmd       启动器
  README.md                      本文件
```

整个文件夹可以直接搬到别的电脑或 U 盘上使用。

### 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11 64 位 |
| 权限 | 普通用户即可，**全程不需要管理员权限** |
| 音频 | 一个麦克风，引擎实际使用 16 kHz 单声道 |
| 网络 | 能访问 `*.doubao.com`（识别在厂商云端完成） |
| 磁盘 | 约 300 MB |

### 设置（`data\config.json`）

| 键 | 默认值 | 含义 |
| --- | --- | --- |
| `hotkey` | `"right alt"` | 按住说话键：`right alt`、`left alt`、`right ctrl`、`left ctrl`、`right shift`、`left shift`、`caps lock`、`left win`、`right win` |
| `suppress_hotkey` | `false` | 设为 `true` 后该键不再传给其他程序（Alt 菜单会失效，谨慎使用） |
| `paste_delay_ms` | `60` | 切回目标窗口与发送 `Ctrl+V` 之间的延时 |
| `restore_clipboard` | `true` | 粘贴后恢复你原来的剪贴板内容 |
| `show_overlay` | `true` | 说话时显示提示条 |
| `final_timeout_sec` | `8.0` | 松手后等待最终结果的最长时间 |
| `min_duration_sec` | `0.35` | 短于该时长的按键忽略 |

改完配置后重启程序生效。

### 开机自启（可选，仍然不需要管理员）

```
app\VoiceInputByRicky.exe --install-autostart
app\VoiceInputByRicky.exe --remove-autostart
```

只会在你自己的启动文件夹写入一个 `.cmd`
（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`），不影响系统其他部分。

### 常见问题

| 现象 | 排查 |
| --- | --- |
| 双击后没有提示条 | 看 `data\logs\app.log` 最后几行；确认整个文件夹都解压完整（不只是 `.cmd`） |
| 有提示条但不出字 | 松手时目标窗口是否处于焦点？以管理员身份运行的窗口会拒绝普通权限程序注入的文字 |
| 显示 `(nothing recognized)` | 检查麦克风音量，以及本机能否访问 `*.doubao.com` |
| 突然完全不能用 | 结束所有**从本文件夹启动**的 `ImeService.exe`，再重新启动程序（程序启动时也会自动回收自己上次残留的引擎进程） |
| 长口述被截断 | 见下面"已知限制"，这是已知问题，不是安装坏了 |

### 已知限制

* **超过约 40 秒的长口述目前不可靠。** 本版本驱动的接口是文件式的：引擎读取一个不断增长的
  WAV，其读取速度在 0.6x–2.5x 实时之间波动，而读到文件结尾就会立即结束会话。彻底解决需要
  以"常驻服务 + 本地管道"的方式驱动引擎（方案 B），见仓库中的
  `Plan B - resident engine prep.md`。
* 识别需要联网，本地不缓存任何识别结果。
* 走的是厂商**私有接口**：豆包输入法将来更新可能让本版本失效。内置引擎副本固定在
  豆包输入法 v0.9.0.0。
* 语种自动识别，没有手动切换开关。

### 卸载

1. 结束 `VoiceInputByRicky.exe`（以及从本文件夹启动的 `ImeService.exe`）。
2. 删除整个文件夹。
3. 如果用过 `--install-autostart`，再删除
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DoubaoVoice.cmd`。

不会写入注册表、服务、计划任务，也不注册输入法。

### 合规提醒

本项目属于个人互操作性质：打包了本机安装的豆包输入法（v0.9.0.0）语音引擎的**副本**，调用
厂商自己的云服务，并依赖未公开的私有接口——厂商条款可能不允许这种做法，仅供你本机自用。
音频会上传到豆包云端，在公司设备上使用前请确认符合公司规定。**请勿再分发打包的引擎文件。**
