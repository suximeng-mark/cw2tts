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

    :param engine: TTS 引擎名称，可选值: auto / edge / winrt / pyttsx3
    :param voice: 语音名称，空字符串表示使用引擎默认语音
    :param volume: 播放音量 0.0 ~ 1.0
    :param templates: 各 activity 类型的朗读文案模板
    """

    engine: str = "auto"
    voice: str = ""
    volume: float = 1.0
    templates: Dict[str, str] = Field(default_factory=dict)

    def __init__(self, **data):
        super().__init__(**data)
        # 确保所有默认模板都存在
        for key, default_text in DEFAULT_TEMPLATES.items():
            if key not in self.templates:
                self.templates[key] = default_text
        logger.debug(
            "[TTS.Config] 创建, engine={}, voice={}, volume={}",
            self.engine,
            self.voice,
            self.volume,
        )
