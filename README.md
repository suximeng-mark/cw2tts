# TTS 自动播报器

> 一个用于 [Class Widgets 2](https://cw.rinlit.cn) 的插件：监听课表通知并自动语音播报。

当课表器触发「上课 / 下课 / 预备铃 / 活动 / 放学」等通知时，插件会把通知内容朗读出来，
支持在线神经语音与多种离线引擎，并允许为每个场景自定义朗读文案与音量。

## 功能特性

- **自动播报**：订阅 `NotificationAPI.pushed`，收到通知即朗读。
- **多引擎**：`auto` 模式按优先级自动选择，四个后端可热切换：
  - `piper` — **本地离线神经语音**（ONNX 推理，默认首选）。音质接近在线引擎，**断网可用**，
    首次需在设置页点「下载」获取模型（约 100MB，之后完全离线）。
  - `edge` — EdgeTTS 在线神经语音（中文推荐 `XiaoxiaoNeural` / `YunxiNeural`），音质最佳，需联网。
  - `winrt` — Windows 系统内置语音（WinRT `SpeechSynthesizer`），离线可用。
  - `pyttsx3` — 跨平台离线引擎，作为最后的兜底。
- **自动故障转移**：`auto` 模式下某引擎合成失败时，自动切换到下一个可用引擎重试。
- **中文数字朗读**：自动把 `14:30`、`3楼201`、`95.5` 转为中文读法，避免误读为英文数字。
- **可配置朗读文案**：为 5 种场景分别设置模板，支持变量替换。
- **音量 / 语音调节**：设置页内实时调节，并支持「测试朗读」。

## 离线朗读（Piper）

`piper` 引擎是本插件的离线朗读器：把文本在本机用神经网络模型合成为语音，
**不上传任何数据、不需要网络**，音质接近在线 EdgeTTS。

- 推理使用 `onnxruntime` 直接运行 Piper ONNX 模型，不引入 GPL-3.0 的 `piper-tts` 包。
- 音素化优先用系统 `espeak-ng`；若系统未安装，会自动回退到 `pypinyin`（音质略降）。
- 模型缓存目录：`%LOCALAPPDATA%\CW2TTS\piper`，在设置页可一键打开。
- 提供两个音色：`zh_CN-huayan-medium`（标准，约 100MB）与 `zh_CN-huayan-x_low`（轻量，老旧机型）。
- 模型需**联网下载一次**，之后永久离线可用。

> Windows 用户若 espeak 音素化不生效，可安装 espeak-ng 并确保其在 `PATH` 中，
> 或设置环境变量 `ESPEAK_PATH` 指向 `espeak-ng.exe`。

## 朗读文案模板

在设置页「朗读文案」中，可为每种场景编辑模板，支持以下变量：

| 变量 | 含义 |
| --- | --- |
| `{title}` | 通知标题（如「上课了」） |
| `{message}` | 通知正文 |
| `{subject}` | 当前科目名 |
| `{teacher}` | 当前教师 |
| `{location}` | 当前地点 |
| `{next_subject}` | 下一节科目名 |
| `{next_teacher}` | 下一节教师 |
| `{next_location}` | 下一节地点 |

- 点击「快速插入变量」按钮可把变量插入到光标处，避免手打变量名出错。
- 每个场景可随时「恢复默认」，也可「试听」当前模板效果。

默认模板：

- 上课：`上课了，{subject}`
- 活动：`活动开始，{subject}`
- 课间休息：`下课了，下节课是{next_subject}`
- 放学：`放学了`
- 预备铃：`预备铃，下节课是{next_subject}`

## 依赖说明

本插件的依赖**已随安装包一起分发**，安装后无需额外 `pip install`：

| 依赖 | 用途 |
| --- | --- |
| `onnxruntime` | Piper 离线语音推理 |
| `edge-tts` | 在线神经语音 |
| `pyttsx3` | 跨平台离线兜底 |
| `winrt-*` | Windows 系统语音 |

> 唯一需要单独下载的是 Piper 语音模型（约 100MB），在设置页点「下载」即可，
> 之后完全离线可用。

## 安装

1. 在 Class Widgets 2 的插件广场中搜索并安装本插件；或下载 `.cwplugin` 文件手动导入。
2. 依赖已内置于安装包，安装后无需额外配置 Python 环境。
3. 首次进入设置页会自动探测可用引擎。若要使用**离线朗读**，在「离线朗读模型」中点「下载」获取 Piper 模型。

## 开发 / 打包 / 发布

```bash
# 1. 安装 SDK 与依赖（开发模式）
pip install -e .

# 2. 在 Class Widgets 2 本体中调试运行本插件

# 3. 打包为可分发包
cw-plugin-pack            # 生成 .cwplugin
cw-plugin-pack --format zip

# 4. 发布：打 tag vX.Y.Z 推送到 GitHub，release.yml 自动构建并发布
git tag v1.0.2 && git push --tags
```

## 致谢

架构参考自插件广场的 [com.hpdnya.tts](https://plaza.cw.rinlit.cn/plugins/com.hpdnya.tts)，
并基于 [Class Widgets 2 官方 SDK](https://github.com/Class-Widgets/class-widgets-sdk) 与
[插件模板](https://github.com/Class-Widgets/plugin-template-v2) 实现。

## 许可证

本插件遵循与 Class Widgets 2 生态一致的 MIT 许可证。
