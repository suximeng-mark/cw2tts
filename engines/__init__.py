"""TTS 引擎注册与工厂。"""

from __future__ import annotations

from typing import Collection, Dict, List, Optional, Tuple, Type

from loguru import logger

from engines.base import TTSEngine
from engines.edge import EdgeTTSEngine
from engines.piper import PiperEngine
from engines.pyttsx3_engine import Pyttsx3Engine
from engines.winsdk_engine import WinRTEngine

# 按优先级排列的引擎列表（auto 模式自上而下尝试）
# Piper 优先：本地神经网络语音，离线可用且音质接近在线引擎。
# 其余为逐级降级：在线 Edge → Windows 系统语音 → SAPI5。
ENGINE_REGISTRY: List[Type[TTSEngine]] = [
    PiperEngine,
    EdgeTTSEngine,
    WinRTEngine,
    Pyttsx3Engine,
]


def create_engine(
    preference: str = "auto",
    voice: str = "",
    excluded: Collection[str] = (),
) -> Optional[TTSEngine]:
    """根据偏好创建 TTS 引擎实例。

    :param preference: "auto" 按优先级自动选择，或指定引擎名称
    :param voice: 语音标识符，空字符串使用默认
    :param excluded: 已失败需跳过的引擎名称集合（故障转移用）
    :return: TTSEngine 实例，全部不可用时返回 None
    """
    logger.debug("[TTS.Engine] create_engine: preference={}, voice={}", preference, voice)
    excluded_names = set(excluded)

    def _instantiate(cls: Type[TTSEngine]) -> Optional[TTSEngine]:
        try:
            return cls(voice=voice) if voice else cls()
        except Exception as e:
            logger.error("[TTS.Engine] 引擎 {} 实例化失败: {}", cls.name, e)
            return None

    if preference != "auto":
        for cls in ENGINE_REGISTRY:
            if cls.name != preference:
                continue
            if cls.name in excluded_names:
                logger.debug("[TTS.Engine] 引擎 {} 已被排除", cls.name)
                return None
            if not cls.is_available():
                logger.warning("[TTS.Engine] 指定引擎 {} 不可用", cls.name)
                return None
            logger.info("[TTS.Engine] 选定引擎: {}", cls.name)
            return _instantiate(cls)
        logger.warning("[TTS.Engine] 未知引擎: {}", preference)
        return None

    for cls in ENGINE_REGISTRY:
        if cls.name in excluded_names:
            logger.debug("[TTS.Engine] auto: 跳过已失败引擎 {}", cls.name)
            continue
        if cls.is_available():
            logger.info("[TTS.Engine] auto 选定引擎: {}", cls.name)
            inst = _instantiate(cls)
            if inst is not None:
                return inst
    logger.warning("[TTS.Engine] 没有可用的 TTS 引擎")
    return None


def list_available_engines() -> List[Dict[str, str]]:
    """列出所有已注册引擎及其可用性。"""
    result: List[Dict[str, str]] = []
    for cls in ENGINE_REGISTRY:
        available = False
        try:
            available = cls.is_available()
        except Exception:
            pass
        result.append({"name": cls.name, "available": str(available).lower()})
    return result


def list_voices_for_engine(engine_name: str) -> List[Tuple[str, str, str]]:
    """列出指定引擎的所有可用语音。"""
    for cls in ENGINE_REGISTRY:
        if cls.name == engine_name:
            try:
                return cls.list_voices()
            except Exception as e:
                logger.warning("[TTS.Engine] 列出 {} 语音失败: {}", engine_name, e)
                return []
    return []


__all__ = [
    "TTSEngine",
    "EdgeTTSEngine",
    "PiperEngine",
    "WinRTEngine",
    "Pyttsx3Engine",
    "ENGINE_REGISTRY",
    "create_engine",
    "list_available_engines",
    "list_voices_for_engine",
]
