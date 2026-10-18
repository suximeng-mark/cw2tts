"""EdgeTTS 后端 — 基于 edge_tts 的在线神经语音合成。

质量最佳（中文推荐 XiaoxiaoNeural / YunxiNeural），但必须联网。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Tuple

from loguru import logger

from engines.base import TTSEngine


class EdgeTTSEngine(TTSEngine):
    """使用 edge_tts 将文本合成为 mp3 文件。"""

    name = "edge"
    DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self._voice = voice or self.DEFAULT_VOICE
        logger.debug("[TTS.Edge] 初始化, voice={}", self._voice)
        import edge_tts  # 延迟导入，避免无网络/未安装时拖累其它引擎

        self._edge_tts = edge_tts

    @staticmethod
    def is_available() -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except ImportError:
            logger.debug("[TTS.Edge] is_available=False, 未安装 edge_tts")
            return False

    @staticmethod
    def list_voices() -> List[Tuple[str, str, str]]:
        try:
            import edge_tts

            voices = asyncio.run(edge_tts.list_voices())
            return [
                (v["ShortName"], f"{v['ShortName']} ({v.get('Locale', '')})", v.get("Locale", ""))
                for v in voices
            ]
        except Exception as e:
            logger.warning("[TTS.Edge] list_voices 失败: {}", e)
            return [("zh-CN-XiaoxiaoNeural", "zh-CN-XiaoxiaoNeural (zh-CN)", "zh-CN")]

    def set_voice(self, voice_id: str) -> None:
        self._voice = voice_id or self.DEFAULT_VOICE
        logger.debug("[TTS.Edge] 语音切换为: {}", self._voice)

    def get_current_voice(self) -> str:
        return self._voice

    def synthesize(self, text: str, out_path: Path) -> None:
        logger.debug("[TTS.Edge] 开始合成, 文本长度={}, 输出={}", len(text), out_path)
        asyncio.run(self._synthesize_async(text, out_path))

    async def _synthesize_async(self, text: str, out_path: Path) -> None:
        communicate = self._edge_tts.Communicate(
            text=text,
            voice=self._voice,
            connect_timeout=8,
            receive_timeout=20,
        )
        await communicate.save(str(out_path))
        logger.debug(
            "[TTS.Edge] 合成完成: {} ({} bytes)",
            out_path,
            out_path.stat().st_size if out_path.exists() else 0,
        )
