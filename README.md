# Voice Input by Ricky

> Hold **Right Alt**, speak, release - the text lands at your cursor.
> Portable Windows voice typing that runs as a standard user: **no installer, no admin
> rights, no changes to Windows input methods.**
>
> 按住 **右 Alt** 说话，松开后文字自动出现在光标处。绿色免安装、**全程普通用户权限**，
> 不注册输入法、不改系统设置。

**[Download the portable build](../../releases/latest)** ·
[Portable package README (EN/中文)](outputs/VoiceInputByRicky/README.md) ·
[Plan B design notes](outputs/Plan%20B%20-%20resident%20engine%20prep.md) ·
[Feasibility report (中文)](outputs/豆包语音通道可行性结论.md)

---

## English

### What it is

A small resident tool that turns speech into text in **any** application: hold Right Alt,
talk, release - the words are typed at the caret. It is not an input method: nothing is
installed, no driver and no TSF registration are involved, so it works on a locked-down
company PC where you cannot get administrator rights.

Under the hood it reuses the speech engine that ships with **Doubao IME** (豆包输入法,
v0.9.0.0) from a *copy* inside the portable folder and drives it in user space. The vendor's
`ImeService.exe` declares `uiAccess="true"`, which Windows refuses to start for a standard
user, so that single attribute is rewritten to `false` **inside this copy only**.

### Download and run

1. Get `VoiceInputByRicky-v*-portable.zip` from [Releases](../../releases/latest) and extract
   it anywhere (a USB stick is fine).
2. Double-click `Voice Input by Ricky.cmd` - no UAC prompt appears.
3. In any text box: **hold Right Alt -> speak -> release**.
4. Quit by ending `VoiceInputByRicky.exe` in Task Manager.

Optional, still no admin rights: `app\VoiceInputByRicky.exe --install-autostart` puts a
launcher in your own Startup folder. See the
[package README](outputs/VoiceInputByRicky/README.md) for settings, troubleshooting and
uninstall.

Requirements: Windows 10/11 x64, a microphone, network access to `*.doubao.com` (recognition
happens in the vendor's cloud), about 300 MB of disk. No Doubao account or login is needed -
authorisation relies on a device id that the engine generates locally.

### Languages

The engine detects the language by itself - no configuration:

| Language | Verified output |
| --- | --- |
| Mandarin | `我确认程序能用，而且不错。请给出下一步的升级迭代规划。` |
| Cantonese speech | `请问听到吗？我讲广东话，一二三。` |
| English | `Good morning, everybody. Today is Sunday. I'm going to go swimming with my daughter...` |

Mandarin, Cantonese and English can be mixed inside one utterance.

### Status

| | |
| --- | --- |
| Short / medium utterances (up to ~40 s) | works, in all three languages |
| Latency | first text ~1.5 s after you start speaking; final text ~1.1-1.7 s after release |
| Very long dictation (>~40 s) | **not reliable yet** - see the limitation below |
| Admin rights | none, ever |
| Install footprint | one folder; no registry keys, services, scheduled tasks or IME registration |

### Known limitation: long dictation

The vendor's file-based test interface reads a *complete* WAV: its read rate varies between
0.6x and 2.5x real time (silence is consumed at full speed, speech is throttled by the
server), and reaching the physical end of the file ends the session immediately. Real-time
streaming through that interface therefore loses audio, which caps the usable utterance at
roughly 40 seconds.

Fixing it properly means driving the engine as a resident server over its local pipe instead
of spawning it per utterance. That work - the private pipe name, the RPC framing and the
probe scripts - is scoped and recorded in
[Plan B - resident engine prep.md](outputs/Plan%20B%20-%20resident%20engine%20prep.md)
(branch `feat/plan-b-prep`).

### Repository layout

```
work/doubao-probe/
  app/            the application source (Python + the frida injection script)
  scripts/        probe, experiment and verification scripts used to reverse the interface
  planb/          Plan B groundwork (branch feat/plan-b-prep): pipe-name patcher,
                  rpc.dll client probe, TSF host harness
  evidence/       before/after registry and process snapshots from the feasibility probe
outputs/
  VoiceInputByRicky/                the portable product layout + its README
  豆包语音通道可行性结论.md           feasibility report of the private channel (中文)
  迭代进展与下一步规划.md             iteration log, measurements and next steps (中文)
  Plan B - resident engine prep.md
```

Vendor binaries, build outputs, user recordings and logs are intentionally **not** in git.

### Build the portable package yourself

The engine binary is not in this repository - it comes from a locally installed Doubao IME:

```
python -m pip install --user pyinstaller frida sounddevice

cd work/doubao-probe
pyinstaller VoiceInputByRicky.spec            # -> dist\VoiceInputByRicky\

# assemble the portable folder
mkdir package\VoiceInputByRicky
xcopy /e /i dist\VoiceInputByRicky                          package\VoiceInputByRicky\app
xcopy /e /i "C:\Program Files\DoubaoIME\versions\v0.9.0.0"   package\VoiceInputByRicky\runtime\v0.9.0.0
python scripts\patch_manifest.py package\VoiceInputByRicky\runtime\v0.9.0.0\ImeService.exe
copy ..\..\outputs\VoiceInputByRicky\README.md                    package\VoiceInputByRicky\
copy "..\..\outputs\VoiceInputByRicky\Voice Input by Ricky.cmd"   package\VoiceInputByRicky\
```

`patch_manifest.py` rewrites `uiAccess="true"` to `uiAccess="false"` (byte-length neutral) and
keeps an `ImeService.exe.orig` backup next to it.

### Compliance

This is a personal interoperability project. It reuses a copy of ByteDance's engine that was
installed locally and talks to the vendor's own cloud service; the vendor's terms may not
allow that, and the interface is private and may break with any update. Audio is uploaded to
Doubao's cloud - check your employer's rules before using it on a work device. Do not
redistribute the bundled engine.

---

## 中文

### 这是什么

一个把语音变成文字的小工具，可以用在**任何**能打字的地方：按住右 Alt 说话、松开，文字就落在
光标处。它不是输入法——不安装任何东西、不注册 TSF、不需要驱动，所以在拿不到管理员权限的
公司电脑上也能用。

原理：用便携目录里**从本机豆包输入法（v0.9.0.0）复制出的一份引擎副本**，以普通用户身份驱动
它。厂商的 `ImeService.exe` 清单写着 `uiAccess="true"`，普通用户被系统拒绝启动，因此在**这份
副本里**把这一个属性等长改写成 `false`。

### 下载与运行

1. 从 [Releases](../../releases/latest) 下载 `VoiceInputByRicky-v*-portable.zip`，解压到任意
   目录（U 盘也行）。
2. 双击 `Voice Input by Ricky.cmd`（不会弹 UAC）。
3. 在任意输入框：**按住右 Alt → 说话 → 松开**。
4. 退出：在任务管理器里结束 `VoiceInputByRicky.exe`。

可选（仍不需要管理员）：`app\VoiceInputByRicky.exe --install-autostart` 会在你自己的启动
文件夹放一个快捷启动脚本。设置项、常见问题、卸载方法见
[便携包 README](outputs/VoiceInputByRicky/README.md)。

系统要求：Windows 10/11 64 位、麦克风、可访问 `*.doubao.com`（识别在云端），磁盘约 300MB。
不需要豆包账号、不需要登录——鉴权用的是引擎在本机生成的设备标识。

### 语言

引擎自己判断语言，无需配置。实测：

| 语言 | 实测输出 |
| --- | --- |
| 普通话 | `我确认程序能用，而且不错。请给出下一步的升级迭代规划。` |
| 粤语口述 | `请问听到吗？我讲广东话，一二三。` |
| 英文 | `Good morning, everybody. Today is Sunday. I'm going to go swimming with my daughter...` |

普通话、粤语、英文混在一句话里也能正确出字。

### 当前状态

| | |
| --- | --- |
| 短句 / 中句（约 40 秒以内） | 可用，三种语言都正常 |
| 延迟 | 开始说话后约 1.5 秒出首字；松手后约 1.1-1.7 秒落地 |
| 超长口述（>约 40 秒） | **尚不可靠**，原因见下 |
| 管理员权限 | 全程不需要 |
| 安装痕迹 | 只有一个文件夹：不写注册表、不装服务、不建计划任务、不注册输入法 |

### 已知限制：长口述

厂商的文件测试接口读的是**完整 WAV**：它的读取速度在 0.6x–2.5x 实时之间波动（读静音全速、
读语音被服务端流控限速），并且**读到物理文件结尾就立即结束会话**。因此通过它做实时流式会丢
音频，可用长度被限制在约 40 秒。

要彻底解决，需要让引擎作为常驻服务运行、改用本地管道驱动它，而不是每次口述都新起一个进程。
这部分工作（私有管道名、RPC 帧格式、探针脚本）已经整理在
[Plan B - resident engine prep.md](outputs/Plan%20B%20-%20resident%20engine%20prep.md)
（分支 `feat/plan-b-prep`）。

### 目录结构

```
work/doubao-probe/
  app/            程序源码（Python + frida 注入脚本）
  scripts/        逆向与验证用的探针、实验脚本
  planb/          方案 B 前期准备（分支 feat/plan-b-prep）：管道改名工具、
                  rpc.dll 客户端探针、TSF 宿主程序
  evidence/       可行性探针前后的注册表 / 进程快照
outputs/
  VoiceInputByRicky/                便携产品目录及其 README
  豆包语音通道可行性结论.md           私有通道可行性报告
  迭代进展与下一步规划.md             迭代记录、实测数据与下一步
  Plan B - resident engine prep.md
```

厂商二进制、构建产物、用户录音与日志**不入库**。

### 自己构建便携包

引擎二进制不在仓库里，需要从本机已安装的豆包输入法复制：

```
python -m pip install --user pyinstaller frida sounddevice

cd work/doubao-probe
pyinstaller VoiceInputByRicky.spec            # 生成 dist\VoiceInputByRicky\

# 组装便携目录
mkdir package\VoiceInputByRicky
xcopy /e /i dist\VoiceInputByRicky                          package\VoiceInputByRicky\app
xcopy /e /i "C:\Program Files\DoubaoIME\versions\v0.9.0.0"   package\VoiceInputByRicky\runtime\v0.9.0.0
python scripts\patch_manifest.py package\VoiceInputByRicky\runtime\v0.9.0.0\ImeService.exe
copy ..\..\outputs\VoiceInputByRicky\README.md                    package\VoiceInputByRicky\
copy "..\..\outputs\VoiceInputByRicky\Voice Input by Ricky.cmd"   package\VoiceInputByRicky\
```

`patch_manifest.py` 会把 `uiAccess="true"` 等长改写成 `uiAccess="false"`，并在旁边保留
`ImeService.exe.orig` 备份。

### 合规提醒

本项目属于个人互操作性质：复用本机已安装引擎的副本，并调用厂商自己的云服务。厂商条款可能不
允许这种做法，且该接口是私有的、可能随版本更新失效。音频会上传到豆包云端，用于公司设备前请
确认符合公司规定；请勿再分发打包的引擎文件。
