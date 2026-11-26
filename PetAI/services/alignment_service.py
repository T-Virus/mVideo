"""音频 / 字幕 / 视频时长自动对齐（PetAI 自有服务，不修改 MPT 核心）。

唯一时间基准是**真实配音文件的时长**，不是脚本里的目标时长：
- 脚本目标时长（例如 45 秒）只用于文案生成，与配音实际长度无关；
- 用户上传配音或 Piper 语速变化后，字幕必须按真实音频重新排布；
- MPT 合成会为素材加安全余量，成片可能比音频长，需裁剪到音频末尾。

对外三个能力：
1. align_srt_to_duration()：把已有 SRT 的时间轴缩放/裁剪到目标时长。
2. align_video_to_audio()：探测成片与音频时长，超差时用 FFmpeg 精确裁剪/补齐。
3. verify_alignment()：返回三者时长与偏差，供界面和日志核查。

全部本地完成：不调用任何在线服务、不产生费用。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PetAI.services.subtitle_service import _fmt_srt_time
from PetAI.utils.safe_log import get_logger

logger = get_logger("alignment")

_TIME_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _to_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000.0


def parse_srt(path: str | Path) -> list[dict[str, Any]]:
    """解析 SRT 为 [{start, end, text}]，容忍 BOM、多余空行与 CRLF。"""
    content = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n"))
    cues: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        # 时间行可能在第一行（无序号）或第二行（标准 SRT）。
        time_index = next(
            (i for i, line in enumerate(lines[:3]) if _TIME_RE.search(line)), None
        )
        if time_index is None:
            continue
        match = _TIME_RE.search(lines[time_index])
        if not match:
            continue
        start = _to_seconds(*match.group(1, 2, 3, 4))
        end = _to_seconds(*match.group(5, 6, 7, 8))
        text = "\n".join(lines[time_index + 1:]).strip()
        if not text:
            continue
        cues.append({"start": start, "end": max(start, end), "text": text})
    cues.sort(key=lambda cue: cue["start"])
    return cues


def write_srt(path: str | Path, cues: list[dict[str, Any]]) -> str:
    """把 cue 列表写回 SRT（覆盖写入，调用方应传新路径或已确认可覆盖）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.extend([
            str(index),
            f"{_fmt_srt_time(cue['start'])} --> {_fmt_srt_time(cue['end'])}",
            cue["text"],
            "",
        ])
    target.write_text("\n".join(lines), encoding="utf-8")
    return str(target)


def align_srt_to_duration(
    srt_path: str | Path,
    target_duration: float,
    output_file: str | Path | None = None,
) -> dict[str, Any]:
    """把字幕时间轴整体缩放到 target_duration，最后一条精确对齐末尾。

    已对齐的字幕（末尾 == 目标时长）不会被改动，只是重写为规范格式。
    缩放会保留原字幕的相对节奏，并消除重叠、倒序与越界。
    """
    source = Path(srt_path)
    cues = parse_srt(source)
    if not cues:
        raise ValueError(f"SRT 中没有有效字幕条目: {source}")
    target = max(0.0, float(target_duration))
    original_end = max(cue["end"] for cue in cues)
    if target <= 0:
        # 目标未知时保持原样，只做规范化。
        target = original_end
    factor = target / original_end if original_end > 0 else 1.0

    aligned: list[dict[str, Any]] = []
    cursor = 0.0
    minimum = 0.12  # 单条字幕最短显示时间，避免缩放后闪现
    for index, cue in enumerate(cues):
        start = max(0.0, cue["start"] * factor)
        end = cue["end"] * factor
        start = max(start, cursor)
        end = max(end, start + minimum)
        if start >= target:
            start = max(0.0, target - minimum)
        end = min(end, target)
        if end <= start:
            end = min(target, start + minimum)
        aligned.append({
            "start": start,
            "end": end,
            "text": cue["text"],
        })
        cursor = end

    if aligned:
        aligned[-1]["end"] = target
        if aligned[-1]["start"] >= target:
            aligned[-1]["start"] = max(0.0, target - minimum)

    destination = Path(output_file) if output_file else source
    write_srt(destination, aligned)
    report = {
        "subtitle_original_end": round(original_end, 3),
        "subtitle_end": round(target, 3),
        "cue_count": len(aligned),
        "scale_factor": round(factor, 4),
        "file": str(destination),
    }
    logger.info(
        "字幕时间轴对齐 cue=%d %.2fs -> %.2fs file=%s",
        len(aligned), original_end, target, destination.name,
    )
    return report


def _ffmpeg_binary() -> str:
    from PetAI.utils.ffmpeg import ffmpeg_binary

    return ffmpeg_binary()


def _has_media_signature(path: Path) -> bool:
    """粗判是否为真实音视频文件，避免对占位文件误调 MoviePy 产生资源告警。"""
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return (
        (header.startswith(b"RIFF") and header[8:12] == b"WAVE")
        or header[4:8] == b"ftyp"
        or header.startswith(b"ID3")
        or header[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    )


def probe_duration(path: str | Path) -> float:
    """探测音视频文件时长（秒）；无法探测时返回 0，不抛错。"""
    media = Path(path)
    if not media.is_file():
        return 0.0

    # 1) ffprobe 最快且最准（多数 FFmpeg 发行版同目录提供）。
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, errors="replace", timeout=30,
            )
            if result.returncode == 0:
                return max(0.0, float(result.stdout.strip()))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    # 2) ffmpeg -i 的 stderr 中有 Duration 行；无输出文件时返回码非 0 属正常。
    try:
        result = subprocess.run(
            [_ffmpeg_binary(), "-hide_banner", "-i", str(media)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=30,
        )
        match = re.search(
            r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", result.stderr
        )
        if match:
            return _to_seconds(match.group(1), match.group(2), match.group(3), "0")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    # 3) 最后兜底：MoviePy（项目已依赖）。先核对文件签名，避免占位文件触发
    #    FFmpeg reader 初始化失败后的析构告警。
    if not _has_media_signature(media):
        return 0.0
    try:
        from moviepy import AudioFileClip, VideoFileClip

        for clip_type in (VideoFileClip, AudioFileClip):
            try:
                with clip_type(str(media)) as clip:
                    duration = float(clip.duration or 0)
                if duration > 0:
                    return duration
            except Exception:
                continue
    except Exception:
        pass
    return 0.0


def align_video_to_audio(
    video_path: str | Path,
    audio_path: str | Path,
    output_file: str | Path | None = None,
    tolerance: float = 0.3,
) -> dict[str, Any]:
    """把成片时长对齐到配音时长；超差时用 FFmpeg 重编码精确裁剪/补齐。

    - 成片更长：-t 裁掉尾部空白画面（MPT 默认加的安全余量）。
    - 成片更短：用 tpad 克隆末帧补足，并保留原配音全长。
    - 差异在容差内或时长无法探测：不重编码，避免无谓画质损失。
    """
    source = Path(video_path)
    target_duration = probe_duration(audio_path)
    video_duration = probe_duration(source)
    report: dict[str, Any] = {
        "audio_duration": round(target_duration, 3),
        "video_duration_before": round(video_duration, 3),
        "action": "none",
        "file": str(source),
    }
    if target_duration <= 0 or video_duration <= 0:
        report["action"] = "skipped_unknown_duration"
        logger.warning("时长探测失败，跳过视频对齐 audio=%.2fs video=%.2fs",
                       target_duration, video_duration)
        return report

    delta = video_duration - target_duration
    if abs(delta) <= max(0.0, float(tolerance)):
        report["video_duration_after"] = round(video_duration, 3)
        return report

    destination = Path(output_file) if output_file else source
    temporary = destination.with_name(
        f"{destination.stem}.align-{destination.suffix or '.mp4'}"
    )
    cmd = [_ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(source)]
    if delta > 0:
        cmd += ["-t", f"{target_duration:.3f}"]
    else:
        cmd += [
            "-vf", f"tpad=stop_mode=clone:stop_duration={abs(delta):.3f}",
            "-t", f"{target_duration:.3f}",
        ]
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-movflags", "+faststart", str(temporary),
    ]
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", timeout=600,
        )
        if result.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
            raise RuntimeError((result.stderr or "FFmpeg 对齐失败").strip()[-500:])
        temporary.replace(destination)
    except Exception as exc:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
        logger.warning("视频时长对齐失败，保留原成片: %s", exc)
        report["action"] = "failed"
        report["error"] = str(exc)
        report["video_duration_after"] = round(video_duration, 3)
        return report

    report.update({
        "action": "trim" if delta > 0 else "extend",
        "delta_before": round(delta, 3),
        "video_duration_after": round(probe_duration(destination), 3),
        "file": str(destination),
    })
    logger.info("视频时长对齐 %s %.2fs -> %.2fs file=%s",
                report["action"], video_duration, report["video_duration_after"],
                destination.name)
    return report


def verify_alignment(
    audio_path: str | Path,
    subtitle_path: str | Path,
    video_path: str | Path,
    tolerance: float = 0.3,
) -> dict[str, Any]:
    """核查三者时长，返回是否与音频对齐（供界面展示与生产后自检）。"""
    audio_duration = probe_duration(audio_path)
    video_duration = probe_duration(video_path)
    subtitle_end = 0.0
    cue_count = 0
    if subtitle_path and Path(subtitle_path).is_file():
        cues = parse_srt(subtitle_path)
        cue_count = len(cues)
        if cues:
            subtitle_end = max(cue["end"] for cue in cues)
    deltas = [
        abs(value - audio_duration)
        for value in (subtitle_end, video_duration)
        if value > 0 and audio_duration > 0
    ]
    return {
        "audio_duration": round(audio_duration, 3),
        "subtitle_end": round(subtitle_end, 3),
        "video_duration": round(video_duration, 3),
        "cue_count": cue_count,
        "max_delta": round(max(deltas), 3) if deltas else None,
        "aligned": bool(deltas) and max(deltas) <= max(0.0, float(tolerance)),
    }
