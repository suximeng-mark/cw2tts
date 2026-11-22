"""朗读调度器 — 合成线程 + QMediaPlayer 统一播放。"""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Optional, Tuple

from loguru import logger
from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from audio_paths import (
    get_audio_dir,
    invalidate_audio_dir,
    is_permission_error,
    new_audio_path,
)
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
            failed_dir: Optional[Path] = None
            for attempt in range(3):
                tmp_path = new_audio_path(self._engine.name, exclude=failed_dir)
                try:
                    self._engine.synthesize(text, tmp_path)
                    break
                except Exception as e:
                    if not is_permission_error(e):
                        raise
                    # 目录被拒（杀软/ACL/占用）：换下一个候选目录重试
                    failed_dir = tmp_path.parent
                    logger.warning(
                        "[TTS.Speaker] 音频目录 {} 写入被拒，换目录重试 ({}/2): {}",
                        failed_dir, attempt + 1, e,
                    )
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    tmp_path = None
                    invalidate_audio_dir(failed_dir)
            else:
                raise RuntimeError(
                    f"所有音频目录均不可写（最后尝试 {get_audio_dir()}），无法合成语音"
                )
            if self.isInterruptionRequested():
                return
            self.playRequested.emit(str(tmp_path))
            tmp_path = None
        except Exception as e:
            logger.error("[TTS.Speaker] 朗读失败: {}", e)
            # 不再清空队列：一次失败不应吞掉后续通知，交给故障转移逐条处理
            if not self.isInterruptionRequested():
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
        # (播放代数, 音频路径)：代数不匹配说明已被新播放取代，避免误删新音频
        self._play_generation = 0
        self._pending_cleanup: Optional[Tuple[int, Path]] = None
        # 切换播放时主动 stop 会产生一次 StoppedState；只忽略那一特定信号，
        # 不能用时间窗口——窗口会把新音频自然结束的信号也吞掉（临时文件残留）。
        self._suppress_next_stop = False
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
        self._player.errorOccurred.connect(self._on_play_error)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state != QMediaPlayer.PlaybackState.StoppedState:
            return
        if self._suppress_next_stop:
            # 切换播放时 stop() 产生的那一次停止：旧文件已在 _play_file 里清理
            self._suppress_next_stop = False
            return
        pending = self._pending_cleanup
        if pending is None:
            return
        generation, tmp = pending
        self._pending_cleanup = None
        if generation != self._play_generation:
            # 旧播放停止信号晚到：旧文件已由 _play_file 主动清理，别动当前音频
            return
        if self._player is not None:
            self._player.setSource(QUrl())  # 先卸载 source 释放文件句柄
        QTimer.singleShot(250, lambda: self._safe_unlink(tmp))

    def _on_play_error(self, error, message: str) -> None:
        """音频设备被独占/文件被锁时给出可定位的日志，不误报为 TTS 权限问题。"""
        logger.warning("[TTS.Speaker] 播放失败: error={}, message={}", error, message)

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
                pending = self._pending_cleanup
                self._pending_cleanup = None
                if pending is not None:
                    self._safe_unlink(pending[1])
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

        # 主动清理上一条遗留；不能只靠 StoppedState，异步信号会误清新 source
        pending = self._pending_cleanup
        if pending is not None:
            self._pending_cleanup = None
            self._safe_unlink(pending[1])

        self._play_generation += 1
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._suppress_next_stop = True
            try:
                self._player.stop()
            except Exception as e:
                self._suppress_next_stop = False
                logger.warning("[TTS.Speaker] 停止上一段播放失败: {}", e)

        self._pending_cleanup = (self._play_generation, audio_path)
        if self._audio_output is not None:
            self._audio_output.setVolume(self._volume)
        self._player.setSource(QUrl.fromLocalFile(str(audio_path)))
        self._player.play()
