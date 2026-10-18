"""插件配置模型。"""

from __future__ import annotations

from typing import Dict

from loguru import logger
from pydantic import Field
from ClassWidgets.SDK import ConfigBaseModel


# provider_id 后缀 → 默认朗读文案模板
# 模板变量:
#   {title}        — 通知标题（如 "上课了"）
#   {message}      — 通知正文（主程序拼好的文本）
#   {subject}      — 当前科目名
#   {teacher}      — 当前教师
#   {location}     — 当前地点
#   {next_subject} — 下一节科目名
#   {next_teacher} — 下一节教师
#   {next_location}— 下一节地点
DEFAULT_TEMPLATES: Dict[str, str] = {
    "class": "上课了，{subject}",
    "activity": "活动开始，{subject}",
    "break": "下课了，下节课是{next_subject}",
    "free": "放学了",
    "preparation": "预备铃，下节课是{next_subject}",
}


class TTSPluginConfig(ConfigBaseModel):
    """TTS 插件配置。

    :param enabled: 总开关，关闭后不再自动播报（仍可在设置页手动试听）
    :param engine: TTS 引擎名称，可选值: auto / piper / edge / winrt / pyttsx3
    :param voice: 语音名称，空字符串表示使用引擎默认语音
    :param volume: 播放音量 0.0 ~ 1.0
    :param rate: 语速倍率，1.0 为正常，0.5~2.0
    :param templates: 各 activity 类型的朗读文案模板
    """

    enabled: bool = True
    engine: str = "auto"
    voice: str = ""
    volume: float = 1.0
    rate: float = 1.0
    templates: Dict[str, str] = Field(default_factory=dict)

    def __init__(self, **data):
        # 与官方模板及广场参考插件一致的写法（均在 Class Widgets 2 内实机运行过）
        super().__init__(**data)
        # 防御：历史配置可能缺字段或类型异常，确保拿到真实 dict
        if not isinstance(self.templates, dict):
            self.templates = {}
        for key, default_text in DEFAULT_TEMPLATES.items():
            if key not in self.templates:
                self.templates[key] = default_text
        # 语速限幅，避免异常值导致引擎报错
        try:
            self.rate = max(0.5, min(2.0, float(self.rate)))
        except (TypeError, ValueError):
            self.rate = 1.0
        logger.debug(
            "[TTS.Config] 创建, enabled={}, engine={}, voice={}, volume={}, rate={}",
            self.enabled,
            self.engine,
            self.voice,
            self.volume,
            self.rate,
        )
