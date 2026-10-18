"""pyttsx3 后端 — 基于 pyttsx3 的本地离线语音合成。

跨平台（Windows 走 SAPI5，其它平台走 espeak/nsss），作为最后的离线兜底。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Tuple

from loguru import logger

from engines.base import TTSEngine


class Pyttsx3Engine(TTSEngine):
    """使用 pyttsx3 将文本合成为 wav 文件。"""

    name = "pyttsx3"

    def __init__(self, voice: str = "") -> None:
        logger.debug("[TTS.pyttsx3] 初始化（引擎将在合成线程中创建）")
        self._engine = None
        self._voice_id = voice

    def _ensure_engine(self):
        if self._engine is None:
            import pyttsx3

            self._engine = pyttsx3.init()
            if self._voice_id:
                self._apply_voice(self._voice_id)
        return self._engine

    def _apply_voice(self, voice_id: str) -> None:
        self._voice_id = voice_id
        if self._engine is None:
            return
        try:
            for v in self._engine.getProperty("voices"):
                if v.id == voice_id or v.name == voice_id:
                    self._engine.setProperty("voice", v.id)
                    self._voice_id = v.id
                    logger.debug("[TTS.pyttsx3] 语音已设置: {}", v.name)
                    return
            logger.warning("[TTS.pyttsx3] 未找到语音: {}, 使用默认", voice_id)
        except Exception as e:
            logger.warning("[TTS.pyttsx3] 设置语音失败: {}", e)

    @staticmethod
    def is_available() -> bool:
        if sys.platform != "win32":
            # 非 Windows 也能用 espeak，但本插件主要面向 Windows 课堂机
            logger.debug("[TTS.pyttsx3] 非 Windows 平台，仍尝试加载")
        try:
            import pyttsx3  # noqa: F401

            return True
        except (ImportError, RuntimeError) as e:
            logger.debug("[TTS.pyttsx3] is_available=False: {}", e)
            return False

    @staticmethod
    def list_voices() -> List[Tuple[str, str, str]]:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            result = []
            for v in engine.getProperty("voices"):
                lang = str(v.languages[0]) if getattr(v, "languages", None) else ""
                display = f"{v.name} ({lang})" if lang else v.name
                result.append((v.id, display, lang))
            engine.stop()
            return result
        except Exception as e:
            logger.warning("[TTS.pyttsx3] list_voices 失败: {}", e)
            return []

    def set_voice(self, voice_id: str) -> None:
        self._apply_voice(voice_id)

    def get_current_voice(self) -> str:
        return self._voice_id

    def synthesize(self, text: str, out_path: Path) -> None:
        logger.debug("[TTS.pyttsx3] 开始合成, 文本长度={}, 输出={}", len(text), out_path)
        engine = self._ensure_engine()
        engine.save_to_file(text, str(out_path))
        engine.runAndWait()
        logger.debug(
            "[TTS.pyttsx3] 合成完成: {} ({} bytes)",
            out_path,
            out_path.stat().st_size if out_path.exists() else 0,
        )

    def stop(self) -> None:
        if self._engine is None:
            return
        try:
            self._engine.stop()
        except Exception as e:
            logger.debug("[TTS.pyttsx3] stop 异常: {}", e)

    def cleanup(self) -> None:
        if self._engine is None:
            return
        try:
            self._engine.stop()
        except Exception:
            pass
        self._engine = None
