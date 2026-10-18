"""Piper 后端 — 基于 ONNX Runtime 的本地神经网络语音合成。

音质接近 EdgeTTS，但完全离线运行，无需联网、无需 API。
使用 onnxruntime 直接推理 Piper 的 ONNX 模型，不引入 GPL-3.0 的 piper-tts 包
（Piper 模型权重本身为 MIT 授权）。

模型在首次使用时自动下载并缓存到用户数据目录，之后完全离线可用。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from loguru import logger

from engines.base import TTSEngine

# 官方语音仓库（rhasspy/piper-voices, v1.0.0 为稳定分支）
VOICE_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

# 内置的常用音色：(voice_id, 模型相对路径, 显示名, 语言, 说明)
BUILTIN_VOICES = [
    (
        "zh_CN-huayan-medium",
        "zh/zh_CN/huayan/medium/zh_CN-huayan-medium",
        "花言 (女声, 标准)",
        "zh_CN",
        "中文神经网络语音，离线可用",
    ),
    (
        "zh_CN-huayan-x_low",
        "zh/zh_CN/huayan/x_low/zh_CN-huayan-x_low",
        "花言·轻量 (女声, 体积最小)",
        "zh_CN",
        "约 25MB，适合老旧课堂机",
    ),
]

_DIGIT_MAP = {
    "0": "零", "1": "一", "2": "二", "3": "三", "4": "四",
    "5": "五", "6": "六", "7": "七", "8": "八", "9": "九",
}


class PiperEngine(TTSEngine):
    """使用 Piper ONNX 模型在本地合成 wav 文件。"""

    name = "piper"

    def __init__(self, voice: str = "zh_CN-huayan-medium") -> None:
        self._voice_id = voice or self.BUILTIN_DEFAULT
        self._model: object = None
        self._config: dict = {}
        self._model_path: Optional[Path] = None
        self._load_lock = threading.Lock()
        logger.debug("[TTS.Piper] 初始化, voice={}", self._voice_id)

    BUILTIN_DEFAULT = "zh_CN-huayan-medium"

    # ---------------------------------------------------------------- 路径

    @staticmethod
    def get_data_dir() -> Path:
        """模型缓存目录（用户可写）。"""
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
        if base:
            root = Path(base)
        else:
            root = Path.home() / ".local" / "share"
        d = root / "CW2TTS" / "piper"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def _voice_files(cls, voice_id: str) -> Optional[Tuple[str, str, str, str, str]]:
        for entry in BUILTIN_VOICES:
            if entry[0] == voice_id:
                return entry
        return None

    # ---------------------------------------------------------------- 依赖

    @staticmethod
    def is_available() -> bool:
        """仅依赖 onnxruntime —— espeak 在合成时才需要，缺失时走 pinyin 回退。"""
        try:
            import onnxruntime  # noqa: F401

            return True
        except ImportError:
            logger.debug("[TTS.Piper] is_available=False, 未安装 onnxruntime")
            return False

    @staticmethod
    def _find_espeak() -> Optional[str]:
        exe = os.environ.get("ESPEAK_PATH")
        if exe and Path(exe).exists():
            return exe
        return shutil.which("espeak-ng") or shutil.which("espeak")

    @staticmethod
    def is_model_downloaded(voice_id: str = BUILTIN_DEFAULT) -> bool:
        entry = PiperEngine._voice_files(voice_id)
        if not entry:
            return False
        d = PiperEngine.get_data_dir()
        return (d / f"{entry[0]}.onnx").exists() and (d / f"{entry[0]}.onnx.json").exists()

    # ---------------------------------------------------------------- 语音

    @staticmethod
    def list_voices() -> List[Tuple[str, str, str]]:
        result = []
        for vid, _path, display, lang, _desc in BUILTIN_VOICES:
            mark = "已下载" if PiperEngine.is_model_downloaded(vid) else "未下载"
            result.append((vid, f"{display} [{mark}]", lang))
        return result

    def set_voice(self, voice_id: str) -> None:
        if voice_id == self._voice_id:
            return
        self._voice_id = voice_id or self.BUILTIN_DEFAULT
        # 切换音色需重载模型
        self.cleanup()
        logger.debug("[TTS.Piper] 语音切换为: {}", self._voice_id)

    def get_current_voice(self) -> str:
        return self._voice_id

    # ---------------------------------------------------------------- 下载

    def ensure_model(self, progress: Optional[Callable[[str, int], None]] = None) -> Path:
        """确保模型已下载，返回 .onnx 路径。"""
        entry = self._voice_files(self._voice_id)
        if not entry:
            raise ValueError(f"未知音色: {self._voice_id}")
        vid, rel, _display, _lang, _desc = entry
        d = self.get_data_dir()
        onnx_path = d / f"{vid}.onnx"
        json_path = d / f"{vid}.onnx.json"
        if onnx_path.exists() and json_path.exists():
            return onnx_path

        import urllib.request

        for suffix, dest in ((".onnx.json", json_path), (".onnx", onnx_path)):
            url = f"{VOICE_BASE_URL}/{rel}{suffix}"
            logger.info("[TTS.Piper] 下载模型文件: {}", url)
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "CW2TTS/1.0"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with open(tmp, "wb") as f:
                        while True:
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(url.rsplit("/", 1)[-1], int(done * 100 / total) if total else 0)
            except Exception as e:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"模型下载失败: {url} — {e}") from e
            tmp.replace(dest)
        logger.info("[TTS.Piper] 模型就绪: {}", onnx_path)
        return onnx_path

    # ---------------------------------------------------------------- 加载

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            onnx_path = self.ensure_model()
            import onnxruntime

            opts = onnxruntime.SessionOptions()
            opts.log_severity_level = 3  # 只保留错误
            opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            self._model = onnxruntime.InferenceSession(
                str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._model_path = onnx_path
            with open(str(onnx_path) + ".json", "r", encoding="utf-8") as f:
                self._config = json.load(f)
            logger.info("[TTS.Piper] 模型加载完成: {}", self._voice_id)

    # ------------------------------------------------------------ 文本归一

    @staticmethod
    def _read_number(raw: str) -> str:
        """把一段纯阿拉伯数字转成中文读法。"""
        digits = [_DIGIT_MAP[c] for c in raw]
        n = len(digits)
        if n == 1:
            return digits[0]
        # 教室号 / 门牌号：三位及以上逐位读（201 → 二零一，1005 → 一零零五）
        if n >= 3:
            return "".join(digits)
        # 两位数：10 → 十，11 → 十一，14 → 十四，20 → 二十，45 → 四十五
        # 个位为「零」时整个省略（十分钟 / 第二十节），十位为一并省略（一十 → 十）
        if digits[1] == "零":
            return "十" if digits[0] == "一" else digits[0] + "十"
        if digits[0] == "一":
            return "十" + digits[1]
        return digits[0] + "十" + digits[1]

    @staticmethod
    def _normalize_numbers(text: str) -> str:
        """把阿拉伯数字转成中文读法，避免 espeak 把 3 读成 "three"。

        采用单次扫描而非多轮 re.sub：多轮替换时，前一轮产出的中文里若含数字
        会被下一轮 \\d+ 当成新数字处理，导致 "14:30" 被读成 "一十四:三零"。
        """
        out: list[str] = []
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch.isdigit():
                j = i
                while j < n and text[j].isdigit():
                    j += 1
                raw = text[i:j]
                # 时间 14:30 —— 小时按数值读（14 → 十四），分钟逐位读（30 → 三零）
                if (
                    j + 2 < n
                    and text[j] == ":"
                    and text[j + 1].isdigit()
                    and text[j + 2].isdigit()
                ):
                    minute = "".join(_DIGIT_MAP[c] for c in text[j + 1 : j + 3].rjust(2, "0"))
                    out.append(f"{PiperEngine._read_number(raw)}点{minute}分")
                    i = j + 3
                    continue
                # 小数 95.5
                if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
                    k = j + 1
                    while k < n and text[k].isdigit():
                        k += 1
                    frac = "".join(_DIGIT_MAP[c] for c in text[j + 1 : k])
                    out.append(f"{PiperEngine._read_number(raw)}点{frac}")
                    i = k
                    continue
                out.append(PiperEngine._read_number(raw))
                i = j
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _phonemize_espeak(self, text: str) -> str:
        exe = self._find_espeak()
        if not exe:
            raise RuntimeError("未找到 espeak-ng")
        cfg = self._config.get("espeak", {})
        voice = cfg.get("voice", "cmn")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            src = f.name
        try:
            proc = subprocess.run(
                [exe, "-v", voice, "-q", "-f", src, "--ipa=1"],
                capture_output=True,
                timeout=60,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"espeak 退出码 {proc.returncode}: {proc.stderr[:200]!r}")
            return proc.stdout.decode("utf-8", errors="replace").strip()
        finally:
            Path(src).unlink(missing_ok=True)

    def _phonemize_pinyin(self, text: str) -> str:
        """espeak 不可用时的回退：直接用拼音引擎生成 IPA。

        piper-tts 的 [zh] 额外依赖（g2pW + transformers）过于沉重，
        这里改用轻量的 pypinyin + 手动映射；若无该依赖则退回原文本。
        """
        try:
            from pypinyin import Style, pinyin
        except ImportError:
            logger.warning("[TTS.Piper] espeak 与 pypinyin 均不可用，音质将明显下降")
            return text

        toks = []
        for py in pinyin(text, style=Style.TONE3, errors="ignore"):
            for item in py:
                toks.append(item)
        return " ".join(toks)

    def _phonemize(self, text: str) -> str:
        text = self._normalize_numbers(text)
        try:
            return self._phonemize_espeak(text)
        except Exception as e:
            logger.debug("[TTS.Piper] espeak 音素化失败，尝试 pinyin 回退: {}", e)
            return self._phonemize_pinyin(text)

    # ---------------------------------------------------------------- 推理

    def _ids_for(self, phonemes: str) -> List[int]:
        symbols = self._config.get("phoneme_id_map") or {}
        pad = int(self._config.get("pad_id", 0))
        eos = int(self._config.get("eos_id", 1))
        unk = int(self._config.get("unk_id", 2))
        # Piper 配置用 IPA 字符 -> id 的映射；连续字符逐个查表
        ids = [symbols.get(ch, unk) for ch in phonemes]
        return [pad] + ids + [eos]

    def synthesize(self, text: str, out_path: Path) -> None:
        logger.debug("[TTS.Piper] 开始合成, 文本长度={}", len(text))
        self._load()
        import numpy as np

        phonemes = self._phonemize(text)
        if not phonemes:
            raise RuntimeError("音素化结果为空")
        ids = np.array([self._ids_for(phonemes)], dtype=np.int64)

        inputs = self._model.get_inputs()
        feed = {inputs[0].name: ids}
        # 部分模型带 input_lengths / 输入长度
        lengths = self._config.get("inference", {})
        noise = lengths.get("noise_scale", 0.667)
        length_scale = lengths.get("length_scale", 1.0)
        noise_w = lengths.get("noise_w", 0.8)
        for meta in inputs:
            if meta.name == "input_lengths":
                feed[meta.name] = np.array([ids.shape[1]], dtype=np.int64)
            elif meta.name == "scales":
                feed[meta.name] = np.array(
                    [noise, length_scale, noise_w], dtype=np.float32
                )

        outputs = self._model.run(None, feed)
        audio = np.asarray(outputs[0], dtype=np.float32).reshape(-1)

        sr = int(self._config.get("audio", {}).get("sample_rate", 22050))
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        logger.debug(
            "[TTS.Piper] 合成完成: {} ({} bytes)",
            out_path,
            out_path.stat().st_size if out_path.exists() else 0,
        )

    def stop(self) -> None:
        # ONNX 推理为一次性调用，无法中途打断；stop 由 speaker 的播放侧处理
        pass

    def cleanup(self) -> None:
        self._model = None
        self._config = {}
        self._model_path = None