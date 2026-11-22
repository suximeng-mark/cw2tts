"""通知文案构建器 — 基于 provider_id、RuntimeAPI 课程信息和模板。"""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from loguru import logger

from tconfig import (
    DEFAULT_TEMPLATES,
    normalize_subject_name,
    normalize_subject_names,
)

# provider_id 后缀 → activity key
_PROVIDER_SUFFIX_MAP: Dict[str, str] = {
    ".class": "class",
    ".activity": "activity",
    ".break": "break",
    ".free": "free",
    ".preparation": "preparation",
}


def resolve_activity_key(provider_id: str) -> Optional[str]:
    """从 provider_id 解析 activity 类型 key。

    例如: "com.classwidgets.schedule.runtime.class" → "class"
    """
    if not provider_id:
        return None
    for suffix, key in _PROVIDER_SUFFIX_MAP.items():
        if provider_id.endswith(suffix):
            return key
    return None


def find_muted_subject(values: Iterable, muted_subjects) -> Optional[str]:
    """在候选科目名里查找命中的禁播项。

    匹配规则（忽略大小写与空白）：候选与名单项相等，或名单项是候选的子串。
    这样「数学」也能覆盖课表里「数学（理科）」之类的写法。
    命中时返回名单里的原始写法，便于日志与提示。
    """
    for value in values:
        candidate = normalize_subject_name(value).casefold()
        if not candidate:
            continue
        for name in normalize_subject_names(muted_subjects):
            key = name.casefold()
            if candidate == key or key in candidate:
                return name
    return None


def find_muted_in_text(text, muted_subjects) -> Optional[str]:
    """文本兜底：runtime 拿不到科目名时，检查通知标题/正文是否提到禁播科目。"""
    body = normalize_subject_name(text).casefold()
    if not body:
        return None
    for name in normalize_subject_names(muted_subjects):
        if name.casefold() in body:
            return name
    return None


def build_announce_text(
    payload: dict,
    templates: Optional[Dict[str, str]] = None,
    runtime_context: Optional[dict] = None,
) -> Optional[str]:
    """根据通知 payload 和模板构建朗读文案。

    :param payload: NotificationAPI.pushed 信号传递的字典
    :param templates: 自定义模板字典 {activity_key: template_str}
    :param runtime_context: 来自 RuntimeAPI 的课程上下文
    :return: 朗读文本，无需朗读时返回 None
    """
    provider_id = str(payload.get("provider_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    message = str(payload.get("message") or "").strip()

    ctx = runtime_context or {}
    subject = str(ctx.get("subject") or "")
    teacher = str(ctx.get("teacher") or "")
    location = str(ctx.get("location") or "")
    next_subject = str(ctx.get("next_subject") or "")
    next_teacher = str(ctx.get("next_teacher") or "")
    next_location = str(ctx.get("next_location") or "")

    activity_key = resolve_activity_key(provider_id)

    if activity_key:
        tmpl_dict = templates if templates else DEFAULT_TEMPLATES
        template = tmpl_dict.get(activity_key, "")
        if not template:
            template = DEFAULT_TEMPLATES.get(activity_key, "{title}。{message}")
        try:
            result = template.format(
                title=title,
                message=message,
                subject=subject,
                teacher=teacher,
                location=location,
                next_subject=next_subject,
                next_teacher=next_teacher,
                next_location=next_location,
            )
            result = result.strip("。，, .").strip()
        except (AttributeError, IndexError, KeyError, ValueError) as e:
            logger.warning("[TTS.Announcer] 模板格式化失败: {}, 回退", e)
            result = f"{title}。{message}" if message else title
        return result if result else None

    # 非日程类通知 — 直接拼接 title + message
    if title and message:
        return f"{title}。{message}"
    return title or message or None
