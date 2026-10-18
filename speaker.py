"""朗读调度器 — 合成线程 + QMediaPlayer 统一播放。"""

from __future__ import annotations

import queue
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger
from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from engines.base import TTSEngine


class SynthesisThread(QThread):
    """串行处理 TTS 后端命令的 Qt 工作线程。"""

    playRequested = Signal(str)
    cleanupRequested = Signal(str)
    synthesisFailed = Signal(str, str, str)

    def __init__(self, engine: TTSEngine, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._requests: "queue.Queue[object]" = queue.Queue()

    @property
    def engine_name(self) -> str:
        return self._engine.name

    @property
    def engine(self) -> TTSEngine:
        return self._engine

    def enqueue_speech(self, text: str) -> None:
        self._requests.put(text)

    def enqueue_voice(self, voice_id: str) -> None:
        self._requests.put(("voice", voice_id))

    def enqueue_engine(self, engine: TTSEngine, retry_text: str) -> None:
        self._requests.put(("engine", engine, retry_text))

    def stop(self) -> None:
        self.requestInterruption()
        self._clear_requests()
        self._requests.put(None)

    def _clear_requests(self) -> None:
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                return

    def run(self) -> None:
        try:
            while not self.isInterruptionRequested():
                request = self._requests.get()
                if request is None:
                    return
                if isinstance(request, tuple):
                    if request[0] == "voice":
                        self._engine.set_voice(str(request[1]))
                    elif request[0] == "engine":
                        _, new_engine, retry_text = request
                        old_engine = self._engine
                        self._engine = new_engine
                        try:
                            old_engine.cleanup()
                        except Exception as e:
                            logger.debug("[TTS.Speaker] 失败引擎清理异常: {}", e)
                        if retry_text and not self.isInterruptionRequested():
                            self._synthesize(str(retry_text))
                    continue
                if isinstance(request, str):
                    self._synthesize(request)
        finally:
            try:
                self._engine.cleanup()
            except Exception as e:
                logger.debug("[TTS.Speaker] 引擎清理异常: {}", e)

    def _synthesize(self, text: str) -> None:
        tmp_path: Optional[Path] = None
        try:
            suffix = ".mp3" if self._engine.name == "edge" else ".wav"
            tmp_path = (
                Path(tempfile.gettempdir())
                / f"cw2_tts_{self._engine.name}_{uuid.uuid4().hex}{suffix}"
            )
            self._engine.synthesize(text, tmp_path)
            if self.isInterruptionRequested():
                return
            self.playRequested.emit(str(tmp_path))
            tmp_path = None
        except Exception as e:
            logger.error("[TTS.Speaker] 朗读失败: {}", e)
            if not self.isInterruptionRequested():
                self._clear_requests()
                self.synthesisFailed.emit(self._engine.name, text, str(e))
        finally:
            if tmp_path is not None:
                self.cleanupRequested.emit(str(tmp_path))


class Speaker(QObject):
    """管理 TTS 合成与 QMediaPlayer 音频播放。

    所有引擎的合成产物都通过 Qt Multimedia 统一播放，无需额外依赖，
    与 Class Widgets 主程序技术栈一致。
    """

    synthesisFailed = Signal(str, str, str)

    def __init__(self, engine: TTSEngine, volume: float = 1.0) -> None:
        super().__init__()
        self._engine = engine
        self._volume = max(0.0, min(1.0, volume))
        self._stopped = False

        self._player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._pending_cleanup: Optional[Path] = None
        self._init_player()

        self._worker = SynthesisThread(engine, self)
        self._worker.playRequested.connect(self._play_file)
        self._worker.cleanupRequested.connect(self._cleanup_file)
        self._worker.synthesisFailed.connect(self.synthesisFailed)
        self._worker.start()

        logger.debug("[TTS.Speaker] 初始化完成, engine={}, volume={}", engine.name, self._volume)

    def _init_player(self) -> None:
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(self._volume)
        self._player.setAudioOutput(self._audio_output)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.StoppedState and self._pending_cleanup is not None:
            tmp = self._pending_cleanup
            self._pending_cleanup = None
            if self._player is not None:
                self._player.setSource(QUrl())  # 先卸载 source 释放文件句柄
            QTimer.singleShot(250, lambda: self._safe_unlink(tmp))

    def _safe_unlink(self, path: Path, retries: int = 8) -> None:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            if retries > 0:
                QTimer.singleShot(500, lambda: self._safe_unlink(path, retries - 1))
                return
            logger.warning("[TTS.Speaker] 清理临时文件失败，重试次数已耗尽: {}", path)
        except Exception as e:
            logger.warning("[TTS.Speaker] 清理临时文件失败: {}", e)

    @Slot(str)
    def _cleanup_file(self, path: str) -> None:
        self._safe_unlink(Path(path))

    @property
    def engine_name(self) -> str:
        return self._worker.engine_name

    @property
    def engine(self) -> TTSEngine:
        return self._worker.engine

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, value))
        if self._audio_output is not None:
            self._audio_output.setVolume(self._volume)

    # ---- public API --------------------------------------------------------

    def speak(self, text: str) -> None:
        text = text.strip()
        if self._stopped or not text:
            return
        self._worker.enqueue_speech(text)

    def set_voice(self, voice_id: str) -> None:
        self._worker.enqueue_voice(voice_id)

    def replace_engine(self, engine: TTSEngine, retry_text: str = "") -> None:
        if self._stopped:
            engine.cleanup()
            return
        self._worker.enqueue_engine(engine, retry_text)

    def shutdown(self) -> None:
        logger.debug("[TTS.Speaker] shutdown 开始")
        self._stopped = True
        self._worker.stop()
        if self._player is not None:
            try:
                self._player.stop()
                self._player.setSource(QUrl())
            except Exception as e:
                logger.warning("[TTS.Speaker] 停止播放异常: {}", e)
        if not self._worker.wait(30000):
            logger.warning("[TTS.Speaker] 合成线程未能在 30 秒内退出")

    @Slot(str)
    def _play_file(self, audio_path_str: str) -> None:
        audio_path = Path(audio_path_str)
        if self._stopped or self._player is None:
            self._safe_unlink(audio_path)
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.stop()
        self._pending_cleanup = audio_path
        if self._audio_output is not None:
            self._audio_output.setVolume(self._volume)
        self._player.setSource(QUrl.fromLocalFile(str(audio_path)))
        self._player.play()
