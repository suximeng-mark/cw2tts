"""音频工作目录管理 — 避开 %TEMP% 权限/占用导致的播报失败。

课堂机上常见：系统临时目录被杀软或组策略限制、临时音频被其他进程占用、
管理员与普通用户切换导致目录 ACL 不一致。本模块按优先级探测一个真正可写的
目录，写入失败时可换下一个候选目录重试。
"""

from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import List, Optional

from loguru import logger

_LOCK = threading.Lock()
_SELECTED_DIR: Optional[Path] = None


def candidate_dirs() -> List[Path]:
    """按优先级返回候选目录：用户专属目录 → 用户目录下的 TEMP 子目录 → 系统 TEMP。"""
    out: List[Path] = []
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if local:
        out.append(Path(local) / "CW2TTS" / "tmp")
    root = Path(tempfile.gettempdir())
    out.append(root / "CW2TTS_TTS")
    out.append(root)
    return out


def _is_writable(directory: Path) -> bool:
    """真实探测：建目录 + 写一个小文件 + 删除，避免 mkdir(exist_ok=True) 的假成功。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".cw2tts_probe_{uuid.uuid4().hex}"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.debug("[TTS.AudioDir] 目录不可写 {}: {}", directory, e)
        return False


def get_audio_dir(exclude: Optional[Path] = None) -> Path:
    """返回当前选中的可写目录；exclude 指向刚失败的目录时重新探测并跳过它。"""
    global _SELECTED_DIR
    with _LOCK:
        if _SELECTED_DIR is not None and (exclude is None or _SELECTED_DIR != exclude):
            return _SELECTED_DIR
        for directory in candidate_dirs():
            if exclude is not None and directory == exclude:
                continue
            if _is_writable(directory):
                _SELECTED_DIR = directory
                logger.info("[TTS.AudioDir] 使用音频目录: {}", directory)
                return directory
        # 全部不可写时返回系统 TEMP，让写入错误照常抛出，由故障转移处理
        _SELECTED_DIR = Path(tempfile.gettempdir())
        logger.warning("[TTS.AudioDir] 所有候选目录均不可写，回退系统 TEMP: {}", _SELECTED_DIR)
        return _SELECTED_DIR


def invalidate_audio_dir(failed: Optional[Path] = None) -> None:
    """权限写入失败后清除缓存；failed 会被后续探测跳过。"""
    global _SELECTED_DIR
    del failed  # 排除由 get_audio_dir(exclude=...) 显式传入，这里只清缓存
    with _LOCK:
        _SELECTED_DIR = None


def new_audio_path(engine_name: str, exclude: Optional[Path] = None) -> Path:
    """生成一个唯一的临时音频路径。"""
    suffix = ".mp3" if engine_name == "edge" else ".wav"
    directory = get_audio_dir(exclude=exclude)
    return directory / f"cw2_tts_{engine_name}_{uuid.uuid4().hex}{suffix}"


def is_permission_error(exc: BaseException) -> bool:
    """识别 Windows / POSIX 的文件权限错误。"""
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "errno", None) in (13, 5, 20)  # EACCES / AccessDenied / ENOTDIR
