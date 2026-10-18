"""TTS 引擎抽象基类。

所有后端只负责把文本合成为音频文件，实际播放由 speaker 模块统一处理。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple


class TTSEngine(ABC):
    """TTS 后端统一接口。"""

    name: str = "base"

    @staticmethod
    @abstractmethod
    def is_available() -> bool:
        """检测当前环境是否支持此后端。"""
        ...

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> None:
        """将文本合成为音频文件（.mp3 / .wav）。

        :param text: 待合成的文本
        :param out_path: 输出音频文件路径
        """
        ...

    @staticmethod
    def list_voices() -> List[Tuple[str, str, str]]:
        """列出此引擎可用的语音 [(voice_id, display_name, locale), ...]。"""
        return []

    def set_voice(self, voice_id: str) -> None:
        """切换当前使用的语音。"""

    def get_current_voice(self) -> str:
        """获取当前使用的语音标识符。"""
        return ""

    def stop(self) -> None:
        """中断当前合成（需要时可覆写）。"""

    def cleanup(self) -> None:
        """释放后端资源（需要时可覆写）。"""
