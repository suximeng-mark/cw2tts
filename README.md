# TTS 自动播报器

> 一个用于 [Class Widgets 2](https://cw.rinlit.cn) 的插件：监听课表通知并自动语音播报。

当课表器触发「上课 / 下课 / 预备铃 / 活动 / 放学」等通知时，插件会把通知内容朗读出来，
支持在线神经语音与多种离线引擎，并允许为每个场景自定义朗读文案与音量。

## 功能特性

- **自动播报**：订阅 `NotificationAPI.pushed`，收到通知即朗读。
- **多引擎**：`auto` 模式按优先级自动选择，三个后端可热切换：
  - `edge` — EdgeTTS 在线神经语音（中文推荐 `XiaoxiaoNeural` / `YunxiNeural`），音质最佳，需联网。
  - `winrt` — Windows 系统内置语音（WinRT `SpeechSynthesizer`），离线可用。
  - `pyttsx3` — 跨平台离线引擎，作为最后的兜底。
- **自动故障转移**：`auto` 模式下某引擎合成失败时，自动切换到下一个可用引擎重试。
- **可配置朗读文案**：为 5 种场景分别设置模板，支持变量替换。
- **音量 / 语音调节**：设置页内实时调节，并支持「测试朗读」。

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

默认模板：

- 上课：`上课了，{subject}`
- 活动：`活动开始，{subject}`
- 课间休息：`下课了，下节课是{next_subject}`
- 放学：`放学了`
- 预备铃：`预备铃，下节课是{next_subject}`

## 安装

1. 在 Class Widgets 2 的插件广场中搜索并安装本插件；或下载 `.cwplugin` 文件手动导入。
2. 首次进入设置页会自动探测可用引擎。若无可用引擎，请确认已安装对应依赖
   （`edge-tts` / `winrt-*` / `pyttsx3`）。

## 开发 / 打包 / 发布

```bash
# 1. 安装 SDK 与依赖（开发模式）
pip install -e .

# 2. 在 Class Widgets 2 本体中调试运行本插件

# 3. 打包为可分发包
cw-plugin-pack            # 生成 .cwplugin
cw-plugin-pack --format zip

# 4. 发布：打 tag vX.Y.Z 推送到 GitHub，release.yml 自动构建并发布
git tag v1.0.0 && git push --tags
```

## 致谢

架构参考自插件广场的 [com.hpdnya.tts](https://plaza.cw.rinlit.cn/plugins/com.hpdnya.tts)，
并基于 [Class Widgets 2 官方 SDK](https://github.com/Class-Widgets/class-widgets-sdk) 与
[插件模板](https://github.com/Class-Widgets/plugin-template-v2) 实现。

## 许可证

本插件遵循与 Class Widgets 2 生态一致的 MIT 许可证。
