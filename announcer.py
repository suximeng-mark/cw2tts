"""通知文案构建器 — 基于 provider_id、RuntimeAPI 课程信息和模板。"""

from __future__ import annotations

from typing import Dict, Optional

from loguru import logger

from tconfig import DEFAULT_TEMPLATES

# provider_id 后缀 → activity key
_PROVIDER_SUFFIX_MAP: Dict[str, str] = {
    ".class": "class",
    ".activity": "activity",
    ".break": "break",
    ".free": "free",
    ".preparation": "preparation",
}


def _resolve_activity_key(provider_id: str) -> Optional[str]:
    """从 provider_id 解析 activity 类型 key。

    例如: "com.classwidgets.schedule.runtime.class" → "class"
    """
    if not provider_id:
        return None
    for suffix, key in _PROVIDER_SUFFIX_MAP.items():
        if provider_id.endswith(suffix):
            return key
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

    activity_key = _resolve_activity_key(provider_id)

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
