"""插件配置模型。"""

from __future__ import annotations

import re
from typing import Any, Dict, List

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

# 禁播科目作用范围：related=当前或下节 / current=仅当前 / next=仅下节
MUTED_SCOPES = ("related", "current", "next")


def normalize_subject_name(value: Any) -> str:
    """科目名归一化：全角空格转半角、去掉全部空白、忽略大小写差异由调用方处理。"""
    if value is None:
        return ""
    text = str(value).replace("\u3000", " ")
    return "".join(text.split())


def normalize_subject_names(values: Any) -> List[str]:
    """把配置里的禁播科目收敛成去重后的字符串列表。

    兼容脏配置：None / 字符串（逗号、顿号、分号或换行分隔）/ 非列表容器。
    """
    if isinstance(values, str):
        candidates: List[Any] = re.split(r"[,，;；、\r\n]+", values)
    elif isinstance(values, (list, tuple, set)):
        candidates = list(values)
    else:
        return []
    out: List[str] = []
    seen = set()
    for item in candidates:
        if not isinstance(item, str):
            continue
        name = normalize_subject_name(item)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def coerce_bool(value: Any, default: bool = True) -> bool:
    """把配置里五花八门的值收敛成 bool。手改配置 / 旧版本迁移是常态。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off", ""):
            return False
    return default


class TTSPluginConfig(ConfigBaseModel):
    """TTS 插件配置。

    :param enabled: 总开关，关闭后不再自动播报（仍可在设置页手动试听）
    :param engine: TTS 引擎名称，可选值: auto / piper / edge / winrt / pyttsx3
    :param voice: 语音名称，空字符串表示使用引擎默认语音
    :param volume: 播放音量 0.0 ~ 1.0
    :param rate: 语速倍率，1.0 为正常，0.5~2.0
    :param templates: 各 activity 类型的朗读文案模板
    :param announce_types: 各 activity 类型是否播报（缺省全部开启）
    :param announce_silent: 是否朗读带 silent 标记的通知
    :param muted_subjects: 不播报的科目名列表（按名称匹配，支持手动添加）
    :param muted_scope: 科目匹配范围 related / current / next
    """

    enabled: bool = True
    engine: str = "auto"
    voice: str = ""
    volume: float = 1.0
    rate: float = 1.0
    templates: Dict[str, str] = Field(default_factory=dict)
    # 用 Any 而不是 bool：脏配置里可能出现 None / "off" / 2，
    # 交给 __init__ 统一收敛，避免 pydantic 校验直接抛异常导致插件加载失败。
    announce_types: Dict[str, Any] = Field(default_factory=dict)
    announce_silent: Any = False
    # 配置由主程序全量反序列化，脏值同样先收下再归一化。
    muted_subjects: Any = Field(default_factory=list)
    muted_scope: Any = "related"

    def __init__(self, **data):
        # 与官方模板及广场参考插件一致的写法（均在 Class Widgets 2 内实机运行过）
        super().__init__(**data)
        # 防御：历史配置可能缺字段或类型异常，确保拿到真实 dict
        if not isinstance(self.templates, dict):
            self.templates = {}
        for key, default_text in DEFAULT_TEMPLATES.items():
            if key not in self.templates:
                self.templates[key] = default_text
        if not isinstance(self.announce_types, dict):
            self.announce_types = {}
        for key in DEFAULT_TEMPLATES:
            self.announce_types[key] = coerce_bool(
                self.announce_types.get(key, True), True
            )
        self.announce_silent = coerce_bool(self.announce_silent, False)
        self.muted_subjects = normalize_subject_names(self.muted_subjects)
        scope = str(self.muted_scope or "").strip().lower()
        self.muted_scope = scope if scope in MUTED_SCOPES else "related"
        # 语速限幅，避免异常值导致引擎报错
        try:
            self.rate = max(0.5, min(2.0, float(self.rate)))
        except (TypeError, ValueError):
            self.rate = 1.0
        logger.debug(
            "[TTS.Config] 创建, enabled={}, engine={}, voice={}, volume={}, rate={}, "
            "muted_subjects={}, muted_scope={}",
            self.enabled,
            self.engine,
            self.voice,
            self.volume,
            self.rate,
            len(self.muted_subjects),
            self.muted_scope,
        )
