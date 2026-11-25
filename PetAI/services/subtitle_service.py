"""本地字幕生成（任务书模块 6/7：自动生成 SRT）。

两条路径，均不调用任何在线/付费服务：
1. script_to_srt（默认）：按标点把脚本切句，按字数比例分配音频时长生成 SRT。
   零依赖、可离线运行、结果可预测，适合配音与字幕同源的场景。
2. whisper_from_audio（显式可选）：faster-whisper 本地模型转写。需用户主动调用，
   不自动下载模型、不上传音频。
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from PetAI.config.settings import PETAI_ROOT
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("subtitle")

# 中英文常见句末标点：用于把脚本切成字幕条。
_SENTENCE_END = re.compile(r"[。！？!?；;]+|\.(?=\s|$)")


def _fmt_srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_sentences(text: str) -> list[str]:
    """按句末标点切句，保留可读长度；过滤空白。"""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    parts = [p.strip() for p in _SENTENCE_END.split(cleaned)]
    sentences = [p for p in parts if p]
    return sentences or [cleaned]


def validate_srt(path: str | Path) -> bool:
    """校验 SRT 是否至少含一条合法时间轴。"""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}", content))


class SubtitleService:
    def __init__(
        self,
        config: Any,
        guard: CostGuard,
        storage_root: str | Path | None = None,
    ) -> None:
        self.config = config
        self.guard = guard
        self.subtitle_root = (
            Path(storage_root) if storage_root else (PETAI_ROOT / "storage" / "videos")
        )
        self.subtitle_root.mkdir(parents=True, exist_ok=True)

    def script_to_srt(
        self,
        script: str,
        audio_duration: float,
        output_file: str | Path | None = None,
        max_chars_per_cue: int = 40,
    ) -> str:
        """按字数比例分配时长生成 SRT。返回生成的 SRT 路径。

        audio_duration <= 0 时按每句 3 秒兜底，避免在未知音频时长下产出 0 时长字幕。
        """
        if audio_duration <= 0:
            logger.warning("音频时长未知，字幕按每句 3 秒兜底分配")
        sentences = split_sentences(script)
        if not sentences:
            raise ValueError("脚本文本为空，无法生成字幕。")

        # 过长句再按长度切分，保证单条字幕可读。
        cues: list[str] = []
        for sentence in sentences:
            while len(sentence) > max_chars_per_cue:
                cut = sentence.rfind("，", 0, max_chars_per_cue)
                if cut < max_chars_per_cue // 2:
                    cut = max_chars_per_cue
                else:
                    cut += 1
                cues.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                cues.append(sentence)

        total_chars = sum(len(c) for c in cues) or 1
        duration = audio_duration if audio_duration > 0 else len(cues) * 3.0
        output = Path(output_file) if output_file else (
            self.subtitle_root / f"subtitle-{uuid.uuid4().hex[:8]}.srt"
        )
        output.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        cursor = 0.0
        for index, cue in enumerate(cues, start=1):
            share = duration * (len(cue) / total_chars)
            end = cursor + share
            if index == len(cues):
                end = duration  # 最后一条对齐音频末尾，消除累积误差
            lines.extend([
                str(index),
                f"{_fmt_srt_time(cursor)} --> {_fmt_srt_time(end)}",
                cue,
                "",
            ])
            cursor = end
        output.write_text("\n".join(lines), encoding="utf-8")
        logger.info("本地字幕生成 cue=%d duration=%.2fs file=%s", len(cues), duration, output.name)
        return str(output)

    def whisper_from_audio(
        self,
        audio_file: str | Path,
        output_file: str | Path | None = None,
        initial_prompt: str = "",
    ) -> str:
        """显式可选的本地 faster-whisper 转写（不联网、不上传音频）。"""
        from app.services import subtitle as mpt_subtitle  # 复用 MPT 本地封装

        audio = Path(audio_file)
        if not audio.is_file():
            raise FileNotFoundError(f"音频文件不存在: {audio}")
        output = Path(output_file) if output_file else audio.with_suffix(".whisper.srt")
        mpt_subtitle.initial_prompt = initial_prompt  # MPT 模块级变量
        result = mpt_subtitle.create(audio_file=str(audio), subtitle_file=str(output))
        if not result or not Path(output).exists():
            raise RuntimeError("faster-whisper 未产出字幕（模型可能未安装，属本地能力缺失）。")
        return str(output)

    validate_srt = staticmethod(validate_srt)
