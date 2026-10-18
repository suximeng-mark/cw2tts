"""TTS 自动播报器。

在 NotificationAPI 的 pushed 信号触发时，按通知级别朗读通知内容。
支持四种后端：Piper（本地离线神经网络）/ EdgeTTS（在线）/ WinRT（Windows 离线）/
pyttsx3（跨平台离线），统一由 QMediaPlayer 播放音频。

特性：
- 动态 TTS 引擎切换与自动故障转移
- 内置 Piper 本地离线模型，断网也能高质量朗读
- 自选 Voice
- 按 activity 自定义朗读文本模板
- 音量调节
"""

from __future__ import annotations

import os
from typing import Optional

from loguru import logger
from PySide6.QtCore import QThread, Signal, Slot

from ClassWidgets.SDK import CW2Plugin, PluginAPI

from announcer import build_announce_text
from engines import create_engine, list_available_engines, list_voices_for_engine
from speaker import Speaker
from tconfig import DEFAULT_TEMPLATES, TTSPluginConfig


class VoiceListThread(QThread):
    """后台获取指定引擎的语音列表。"""

    loaded = Signal(int, list)

    def __init__(self, generation: int, engine_name: str, parent=None) -> None:
        super().__init__(parent)
        self._generation = generation
        self._engine_name = engine_name

    def run(self) -> None:
        voices: list[dict[str, str]] = []
        try:
            if self._engine_name and self._engine_name != "auto":
                raw = list_voices_for_engine(self._engine_name)
                voices = [
                    {"id": v[0], "name": v[1], "locale": v[2] if len(v) > 2 else ""}
                    for v in raw
                ]
            logger.debug("[TTS] 语音列表刷新完成, {} 条", len(voices))
        except Exception as e:
            logger.warning("[TTS] 语音列表刷新失败: {}", e)
        self.loaded.emit(self._generation, voices)


class ModelDownloadThread(QThread):
    """后台下载 Piper 模型（首次使用约 100MB），避免阻塞界面。"""

    progress = Signal(str, int)
    finished_ok = Signal(bool, str)

    def __init__(self, voice_id: str, parent=None) -> None:
        super().__init__(parent)
        self._voice_id = voice_id

    def run(self) -> None:
        try:
            from engines.piper import PiperEngine

            engine = PiperEngine(voice=self._voice_id)
            engine.ensure_model(progress=lambda name, pct: self.progress.emit(name, pct))
            self.finished_ok.emit(True, "模型已就绪")
        except Exception as e:
            logger.warning("[TTS] 模型下载失败: {}", e)
            self.finished_ok.emit(False, str(e))


class Plugin(CW2Plugin):
    # 通知 QML 刷新的信号
    engineChanged = Signal()
    voiceListChanged = Signal()
    configChanged = Signal()
    modelProgressChanged = Signal(str, int)

    def __init__(self, api: PluginAPI) -> None:
        logger.debug("[TTS] Plugin.__init__ 开始")
        super().__init__(api)
        self._config = TTSPluginConfig()
        self._speaker: Optional[Speaker] = None
        self._voice_cache: list[dict[str, str]] = []
        self._voices_loading = False
        self._voice_load_generation = 0
        self._failed_engines: set[str] = set()
        self._voice_thread: Optional[VoiceListThread] = None
        self._model_thread: Optional[ModelDownloadThread] = None
        logger.debug("[TTS] Plugin.__init__ 完成, 默认引擎={}", self._config.engine)

    # ---- 生命周期 ----------------------------------------------------------

    def on_load(self) -> None:
        logger.info("[TTS] on_load 开始, pid={}", self.meta.get("id"))
        super().on_load()

        try:
            self.api.config.register_plugin_model(self.pid, self._config)
            logger.debug("[TTS] 配置模型注册成功")
        except Exception as e:
            logger.error("[TTS] 注册配置模型失败: {}", e)
            raise

        self._register_settings_page()
        self._init_speaker()

        self.api.notification.pushed.connect(self._on_notification_pushed)
        logger.debug("[TTS] 已连接 notification.pushed 信号")

        backend = self._speaker.engine_name if self._speaker else "none"
        logger.info("[TTS] TTS 自动播报器已加载 (backend={})", backend)

    def on_unload(self) -> None:
        logger.info("[TTS] on_unload 开始")
        try:
            self.api.notification.pushed.disconnect(self._on_notification_pushed)
        except Exception as e:
            logger.debug("[TTS] 断开信号时异常 (可忽略): {}", e)

        if self._speaker is not None:
            self._speaker.shutdown()
            self._speaker = None

        if self._voice_thread is not None:
            self._voice_thread.requestInterruption()
            if not self._voice_thread.wait(30000):
                logger.warning("[TTS] 语音列表线程未能在 30 秒内退出")
            self._voice_thread = None

        if self._model_thread is not None:
            self._model_thread.requestInterruption()
            if not self._model_thread.wait(5000):
                logger.warning("[TTS] 模型下载线程未能在 5 秒内退出")
            self._model_thread = None

        logger.info("[TTS] TTS 自动播报器已卸载")

    # ---- QML Slots ---------------------------------------------------------

    @Slot(result=list)
    def getAvailableEngines(self) -> list:
        return [{"name": "auto", "available": "true"}] + list_available_engines()

    @Slot(result=str)
    def getCurrentEngine(self) -> str:
        return self._config.engine

    @Slot(str)
    def setEngine(self, engine_name: str) -> None:
        logger.info("[TTS] setEngine: {}", engine_name)
        if engine_name == self._config.engine:
            return
        self._config.engine = engine_name
        self._config.voice = ""
        self._failed_engines.clear()
        self._save_config()

        self._init_speaker()
        self.engineChanged.emit()

        if self._voice_thread is not None:
            self._voice_thread.requestInterruption()
        self._voice_cache = []
        self._voice_load_generation += 1
        self._voices_loading = False
        self.voiceListChanged.emit()
        self.configChanged.emit()

    @Slot(result=list)
    def getVoiceList(self) -> list:
        return self._voice_cache

    @Slot()
    def refreshVoiceList(self) -> None:
        if self._voices_loading:
            return
        self._voices_loading = True
        self._voice_load_generation += 1
        generation = self._voice_load_generation
        engine_name = self._config.engine
        if engine_name == "auto" and self._speaker:
            engine_name = self._speaker.engine_name

        thread = VoiceListThread(generation, engine_name, self)
        thread.loaded.connect(self._finish_voice_refresh)
        thread.finished.connect(self._on_voice_thread_finished)
        self._voice_thread = thread
        thread.start()

    @Slot()
    def _on_voice_thread_finished(self) -> None:
        thread = self.sender()
        if thread is self._voice_thread:
            self._voice_thread = None
        if thread is not None:
            thread.deleteLater()

    @Slot(int, list)
    def _finish_voice_refresh(self, generation: int, voices: list) -> None:
        if generation != self._voice_load_generation:
            return
        self._voice_cache = voices
        self._voices_loading = False
        self.voiceListChanged.emit()

    @Slot(result=str)
    def getCurrentVoice(self) -> str:
        return self._config.voice

    @Slot(str)
    def setVoice(self, voice_id: str) -> None:
        logger.info("[TTS] setVoice: {}", voice_id)
        self._config.voice = voice_id
        self._save_config()
        if self._speaker:
            self._speaker.set_voice(voice_id)
        self.configChanged.emit()

    @Slot(result=float)
    def getVolume(self) -> float:
        return self._config.volume

    @Slot(float)
    def setVolume(self, volume: float) -> None:
        volume = max(0.0, min(1.0, volume))
        self._config.volume = volume
        self._save_config()
        if self._speaker:
            self._speaker.volume = volume
        self.configChanged.emit()

    @Slot(result=list)
    def getTemplateKeys(self) -> list:
        return list(DEFAULT_TEMPLATES.keys())

    @Slot(str, result=str)
    def getTemplate(self, key: str) -> str:
        return self._config.templates.get(key, DEFAULT_TEMPLATES.get(key, ""))

    @Slot(str, str)
    def setTemplate(self, key: str, template: str) -> None:
        logger.debug("[TTS] setTemplate: {}={!r}", key, template)
        templates = dict(self._config.templates)
        templates[key] = template
        self._config.templates = templates
        self._save_config()
        self.configChanged.emit()

    @Slot(str, result=str)
    def getDefaultTemplate(self, key: str) -> str:
        return DEFAULT_TEMPLATES.get(key, "")

    @Slot(str)
    def resetTemplate(self, key: str) -> None:
        default = DEFAULT_TEMPLATES.get(key, "")
        templates = dict(self._config.templates)
        templates[key] = default
        self._config.templates = templates
        self._save_config()
        self.configChanged.emit()

    @Slot(str)
    def testSpeak(self, text: str) -> None:
        if self._speaker and text:
            self._speaker.speak(text)

    @Slot(result=str)
    def getActiveEngineName(self) -> str:
        if self._speaker:
            return self._speaker.engine_name
        return "none"

    # ---- Piper 模型管理 ----------------------------------------------------

    @Slot(result=str)
    def getPiperModelState(self) -> str:
        """返回 piper 模型状态：ready / missing / unavailable。"""
        try:
            from engines.piper import PiperEngine

            if not PiperEngine.is_available():
                return "unavailable"
            voice = self._config.voice or PiperEngine.BUILTIN_DEFAULT
            return "ready" if PiperEngine.is_model_downloaded(voice) else "missing"
        except Exception as e:
            logger.debug("[TTS] getPiperModelState 异常: {}", e)
            return "unavailable"

    @Slot(result=str)
    def getPiperModelPath(self) -> str:
        try:
            from engines.piper import PiperEngine

            return str(PiperEngine.get_data_dir())
        except Exception:
            return ""

    @Slot()
    def downloadPiperModel(self) -> None:
        """后台下载 Piper 模型，进度经 modelProgressChanged 回传。"""
        if self._model_thread is not None:
            return
        try:
            from engines.piper import PiperEngine

            if not PiperEngine.is_available():
                logger.warning("[TTS] 未安装 onnxruntime，无法下载模型")
                return
            voice = self._config.voice or PiperEngine.BUILTIN_DEFAULT
        except Exception as e:
            logger.warning("[TTS] downloadPiperModel 前置检查失败: {}", e)
            return

        thread = ModelDownloadThread(voice, self)
        thread.progress.connect(self.modelProgressChanged)
        thread.finished_ok.connect(self._on_model_download_done)
        thread.finished.connect(self._on_model_thread_finished)
        self._model_thread = thread
        thread.start()

    @Slot(bool, str)
    def _on_model_download_done(self, ok: bool, message: str) -> None:
        if ok:
            logger.info("[TTS] Piper 模型下载完成")
            self.modelProgressChanged.emit("", 100)
            self.voiceListChanged.emit()
            self.engineChanged.emit()
        else:
            logger.warning("[TTS] Piper 模型下载失败: {}", message)

    @Slot()
    def _on_model_thread_finished(self) -> None:
        self._model_thread = None

    # ---- 内部方法 ----------------------------------------------------------

    def _init_speaker(self) -> None:
        if self._speaker is not None:
            self._speaker.shutdown()
            self._speaker = None

        engine = create_engine(self._config.engine, self._config.voice)
        if engine is not None:
            self._speaker = Speaker(engine, volume=self._config.volume)
            self._speaker.synthesisFailed.connect(self._on_synthesis_failed)
            logger.debug("[TTS] Speaker 初始化完成, engine={}", engine.name)
        else:
            logger.warning("[TTS] 未能创建任何 TTS 引擎, 语音播报将不可用")

    @Slot(str, str, str)
    def _on_synthesis_failed(self, engine_name: str, text: str, error: str) -> None:
        logger.warning("[TTS] 引擎 {} 合成失败: {}", engine_name, error)
        if self._config.engine != "auto" or self._speaker is None:
            return

        self._failed_engines.add(engine_name)
        replacement = create_engine("auto", excluded=self._failed_engines)
        if replacement is None:
            logger.error("[TTS] 所有 TTS 引擎均不可用，放弃本次朗读")
            return

        logger.warning("[TTS] 自动故障转移: {} -> {}", engine_name, replacement.name)
        self._speaker.replace_engine(replacement, retry_text=text)
        self.engineChanged.emit()
        self._voice_cache = []
        self._voice_load_generation += 1
        self._voices_loading = False
        self.voiceListChanged.emit()

    def _save_config(self) -> None:
        try:
            self.api.config.save()
        except Exception as e:
            logger.warning("[TTS] 保存配置失败: {}", e)

    def _register_settings_page(self) -> None:
        try:
            settings_qml = os.path.join(os.path.dirname(__file__), "qml", "settings.qml")
            self.api.ui.register_settings_page(
                qml_path=settings_qml,
                title="TTS 自动播报",
                icon="ic_fluent_speaker_2_20_regular",
            )
            logger.debug("[TTS] 设置页注册成功")
        except Exception as e:
            logger.warning("[TTS] 注册设置页失败: {}", e)

    def _on_notification_pushed(self, payload: dict) -> None:
        logger.debug(
            "[TTS] 收到通知: provider_id={}, title={!r}, message={!r}",
            payload.get("provider_id"),
            payload.get("title"),
            payload.get("message"),
        )
        if self._speaker is None:
            logger.warning("[TTS] Speaker 为 None, 跳过朗读")
            return
        if payload.get("silent", False):
            logger.debug("[TTS] 通知标记为 silent, 跳过朗读")
            return

        runtime_context = self._build_runtime_context()
        text = build_announce_text(
            payload,
            templates=self._config.templates,
            runtime_context=runtime_context,
        )
        logger.debug("[TTS] 构建朗读文案: {!r}", text)
        if text:
            self._speaker.speak(text)

    def _build_runtime_context(self) -> dict:
        """从 RuntimeAPI 提取当前/下一节课程的结构化信息。"""
        ctx: dict = {
            "subject": "",
            "teacher": "",
            "location": "",
            "next_subject": "",
            "next_teacher": "",
            "next_location": "",
        }
        try:
            runtime = self.api.runtime

            cur_subject = runtime.current_subject
            if cur_subject:
                ctx["subject"] = cur_subject.get("name", "") or ""
                ctx["teacher"] = cur_subject.get("teacher", "") or ""
                ctx["location"] = cur_subject.get("location", "") or ""

            cur_entry = runtime.current_entry
            if cur_entry and not ctx["subject"]:
                ctx["subject"] = cur_entry.get("title", "") or ""

            next_entries = runtime.next_entries
            if next_entries:
                next_entry = next_entries[0]
                next_sid = next_entry.get("subjectId")
                if next_sid:
                    try:
                        schedule_data = self.api.schedule.get()
                        subjects = getattr(schedule_data, "subjects", None)
                        if subjects:
                            for s in subjects:
                                sd = s.model_dump() if hasattr(s, "model_dump") else s
                                if sd.get("id") == next_sid:
                                    ctx["next_subject"] = sd.get("name", "") or ""
                                    ctx["next_teacher"] = sd.get("teacher", "") or ""
                                    ctx["next_location"] = sd.get("location", "") or ""
                                    break
                    except Exception as e:
                        logger.debug("[TTS] 获取下一节 subject 详情失败: {}", e)
                if not ctx["next_subject"]:
                    ctx["next_subject"] = next_entry.get("title", "") or ""
        except Exception as e:
            logger.warning("[TTS] 获取 runtime 课程信息失败: {}", e)

        logger.debug("[TTS] runtime_context: {}", ctx)
        return ctx
