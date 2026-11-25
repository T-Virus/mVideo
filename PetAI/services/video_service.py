"""视频生产中心（任务书模块 7/8）。

设计原则：
1. 复用 MoneyPrinterTurbo 现有合成引擎（combine_videos + generate_video），
   不改任何 MPT 核心代码；合成函数通过构造参数注入，便于离线测试。
2. 默认模式 A（零成本）：豆包生成/本地上传的素材 + 本地配音 + 本地字幕 + FFmpeg 合成。
   图片素材会先在本地转成静帧视频再交给 MPT；绝不调用付费 AI 图片/视频服务。
3. 固定 9:16 竖屏（VideoAspect.portrait → 1080×1920）。
4. 中英文独立成片，各自记录生产状态，支持重新合成。

合成链路由 MPT 提供：
    MPT combine_videos(无声素材视频) -> MPT generate_video(画面+音频+字幕 -> MP4)
PetAI 只负责：选素材、本地配音、本地字幕、组装参数、落库与产物管理。
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from PetAI.config.settings import PETAI_ROOT, Service
from PetAI.database import repository as repo
from PetAI.services.alignment_service import (
    align_srt_to_duration,
    align_video_to_audio,
    verify_alignment,
)
from PetAI.services.cost_service import CostGuard
from PetAI.services.local_asset_service import LocalAssetService
from PetAI.services.subtitle_service import SubtitleService, validate_srt
from PetAI.services.tts_service import TTSService
from PetAI.utils.safe_log import get_logger

logger = get_logger("video")

Status = str  # pending / running / done / failed

# 豆包生成的画面素材多为图片，而 MPT combine_videos 只接受视频文件。
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class VideoService:
    def __init__(
        self,
        config: Any,
        guard: CostGuard,
        assets: LocalAssetService,
        tts: TTSService,
        subtitles: SubtitleService,
        storage_root: str | Path | None = None,
        combine_videos: Callable[..., str] | None = None,
        generate_video: Callable[..., bool] | None = None,
        image_transcode: bool = True,
    ) -> None:
        self.config = config
        self.guard = guard
        self.assets = assets
        self.tts = tts
        self.subtitles = subtitles
        self.export_root = (
            Path(storage_root) if storage_root else (PETAI_ROOT / "storage" / "exports")
        )
        self.export_root.mkdir(parents=True, exist_ok=True)
        # 惰性导入 MPT 引擎：只有真正合成时才加载，测试可用 mock 注入。
        self._combine_videos = combine_videos
        self._generate_video = generate_video
        # 离线测试可关闭真实转码；生产环境保持开启，确保图片素材能进合成。
        self.image_transcode = bool(image_transcode)

    # ---------- MPT 引擎（惰性解析，不改其核心） ----------
    def _load_engine(self) -> None:
        if self._combine_videos is None or self._generate_video is None:
            from app.services import video as mpt_video

            self._combine_videos = self._combine_videos or mpt_video.combine_videos
            self._generate_video = self._generate_video or mpt_video.generate_video

    def build_video_params(self, language: str = "zh") -> Any:
        """构造 MPT VideoParams：固定 9:16 竖屏 + 顺序拼接 + 本地字幕字体。"""
        from app.models.schema import VideoAspect, VideoConcatMode, VideoParams

        width = int(self.config.get("video", "width", 1080))
        height = int(self.config.get("video", "height", 1920))
        aspect = VideoAspect.portrait if (width, height) == (1080, 1920) else VideoAspect(
            str(self.config.get("video", "aspect", "9:16"))
        )
        subtitle_enabled = bool(self.config.get("video", "subtitle_enabled", True))
        return VideoParams(
            video_subject="pet content",
            video_aspect=aspect,                       # portrait → (1080, 1920)
            video_script="",                           # 合成阶段不需要脚本文本
            video_source="local",
            # 按分镜顺序播放；random 会打乱镜头，导致画面与口播对不上。
            video_concat_mode=VideoConcatMode.sequential,
            video_clip_duration=int(self.config.get("video", "clip_duration", 5)),
            subtitle_enabled=subtitle_enabled,
            font_name=str(self.config.get("video", "font_name", "STHeitiMedium.ttc")),
            bgm_type=str(self.config.get("video", "bgm_type", "random")),
            voice_name="",                             # 配音已由 PetAI 本地生成
        )

    # ---------- 图片素材本地转视频 ----------
    # 同一素材复用时，按这些区域裁出不同画面（9:16 基准）
    _VARIANT_CROPS = (
        "crop=iw*0.55:ih*0.55:iw*0.225:ih*0.225",   # 中心
        "crop=iw*0.55:ih*0.55:0:ih*0.225",           # 左
        "crop=iw*0.55:ih*0.55:iw*0.45:ih*0.225",     # 右
        "crop=iw*0.55:ih*0.55:iw*0.225:0",           # 上
        "crop=iw*0.55:ih*0.55:iw*0.225:ih*0.45",     # 下
    )

    def _diversify_materials(
        self, materials: list[dict[str, Any]], production_id: str
    ) -> list[dict[str, Any]]:
        """素材少于分镜时，对重复使用的素材做不同裁剪，避免整条视频同一画面。"""
        seen: dict[str, int] = {}
        result: list[dict[str, Any]] = []
        for item in materials:
            path = str(item.get("path", ""))
            index = seen.get(path, 0)
            seen[path] = index + 1
            if index == 0 or not path:
                result.append(item)
                continue
            variant = self._crop_variant(Path(path), index, production_id)
            result.append({**item, "path": str(variant)} if variant else item)
        return result

    def _crop_variant(self, source: Path, index: int, production_id: str) -> Path | None:
        """用本地 FFmpeg 裁出不同区域并统一到 9:16，作为同素材的镜头变体。"""
        if not source.is_file():
            return None
        try:
            from app.utils import utils as mpt_utils

            ffmpeg = mpt_utils.get_ffmpeg_binary()
        except Exception:
            return None
        frames = self.export_root / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        out = frames / f"variant-{production_id[:8]}-{index}.mp4"
        if out.is_file() and out.stat().st_size > 0:
            return out
        crop = self._VARIANT_CROPS[index % len(self._VARIANT_CROPS)]
        duration = int(self.config.get("video", "clip_duration", 5))
        vf = (
            f"{crop},scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        )
        if source.suffix.lower() in IMAGE_SUFFIXES:
            input_args = ["-loop", "1", "-i", str(source)]
        else:
            input_args = ["-stream_loop", "-1", "-i", str(source)]
        cmd = [
            ffmpeg, "-y", *input_args, "-t", str(duration), "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-an", str(out),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except Exception:
            return None
        return out if proc.returncode == 0 and out.is_file() else None

    def _video_material_paths(
        self, selected: list[dict[str, Any]], production_id: str
    ) -> list[str]:
        """把图片素材用本地 FFmpeg 转成竖屏静帧视频，供 MPT 合成使用。

        桌面豆包主要产出图片，而 MPT 的 combine_videos 只接受视频文件。
        这一步 100% 本地完成，不调用任何 AI 服务、不产生费用。
        """
        paths: list[str] = []
        for index, item in enumerate(selected):
            path = Path(str(item.get("path", "")))
            if not path.is_file():
                continue
            if path.suffix.lower() in IMAGE_SUFFIXES and self.image_transcode:
                converted = self._image_to_clip(path, production_id, index)
                if converted:
                    paths.append(converted)
            else:
                paths.append(str(path))
        return paths

    def _image_to_clip(self, image: Path, production_id: str, index: int) -> str:
        """用项目自带的 FFmpeg 把单张图片编码成 9:16 静帧 mp4。"""
        from app.utils import utils as mpt_utils

        ffmpeg = mpt_utils.get_ffmpeg_binary()
        frames_dir = self.export_root / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        out = frames_dir / f"frame-{production_id[:8]}-{index}.mp4"
        width = int(self.config.get("video", "width", 1080) or 1080)
        height = int(self.config.get("video", "height", 1920) or 1920)
        seconds = max(1.0, float(self.config.get("video", "clip_duration", 5) or 5))
        cmd = [
            ffmpeg, "-y", "-loop", "1", "-t", str(seconds), "-i", str(image),
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
            "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
        ]
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=180, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"图片素材转本地视频失败：{exc}") from exc
        if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            detail = ""
            if proc.stderr:
                detail = proc.stderr.decode("utf-8", "replace")[-300:]
            raise RuntimeError(f"图片素材转本地视频失败（FFmpeg）：{detail}")
        logger.info("图片素材已转本地静帧视频 %s -> %s", image.name, out.name)
        return str(out)

    # ---------- 生产主流程 ----------
    def produce(
        self,
        topic_id: str,
        language: str,
        script: str,
        materials: list[dict[str, Any]] | None = None,
        voice_file: str | None = None,
        audio_duration: float = 0.0,
        bgm_file: str = "",
        task_id: str = "",
        subtitles_mode: str = "script",  # script / whisper / none
        storyboard: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """生产一条视频。默认走低成本模式 A。

        materials: 显式素材（含 path/kind）；为空时按 storyboard 逐镜配齐。
        storyboard: 分镜方案；提供时会按镜头顺序配素材并顺序拼接。
        subtitles_mode=none 时不生成字幕（subtitle_path 传空串给 MPT）。
        """
        self.guard.require(Service.LOCAL_TTS)  # 本地配音闸门
        self._assert_no_ai_media()             # 再次确认未启用付费 AI 画面

        production_id = uuid.uuid4().hex
        self._save(production_id, topic_id, task_id, language, "running",
                   script_id="", tts_source="", error="")
        try:
            # 1) 素材：显式传入优先；否则按分镜逐镜配齐；最后才退回本地单素材。
            #    绝不自动下载网络素材。
            if materials:
                selected = materials
            elif storyboard:
                selected = self.assets.plan_for_storyboard(storyboard)
            else:
                selected = self.assets.select_local_first(animal="both", count=6)
            if not selected:
                raise RuntimeError(
                    "本地素材库为空。可上传素材，或在向导第 4 步用「按分镜用豆包生成素材」；"
                    "系统不会自动下载网络素材。"
                )
            # 素材少于分镜时，对重复素材做不同裁剪，避免整条视频同一画面。
            selected = self._diversify_materials(selected, production_id)
            # 图片素材（含豆包生成）先本地转成静帧视频，MPT 才能拼接。
            material_paths = self._video_material_paths(selected, production_id)
            if not material_paths:
                raise RuntimeError("所选素材文件均不存在，无法合成。")

            # 2) 本地配音：voice_file 存在则直接用；否则 Piper；失败明确报错不回退。
            audio_dir = PETAI_ROOT / "storage" / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_path = audio_dir / f"voice-{production_id[:8]}.wav"
            tts_result = self.tts.synthesize(
                text=script,
                language=language,
                output_file=audio_path,
                voice_file=voice_file,
                task_id=task_id,
            )
            audio_final = tts_result["audio_path"]

            # 3) 时长唯一基准：真实配音文件。脚本目标时长只用于文案生成，
            #    探测失败时才退回调用方传入值，避免字幕与成片按错误时长排布。
            real_duration = self._audio_duration(audio_final)
            duration = real_duration if real_duration > 0 else float(audio_duration or 0)
            auto_align, tolerance = self._alignment_settings()

            # 4) 本地字幕。
            subtitle_path = ""
            if subtitles_mode == "script":
                subtitle_path = self.subtitles.script_to_srt(
                    script=script,
                    audio_duration=duration,
                    output_file=self.export_root / f"subtitle-{production_id[:8]}.srt",
                )
            elif subtitles_mode == "whisper":
                subtitle_path = self.subtitles.whisper_from_audio(
                    audio_file=audio_final,
                    output_file=self.export_root / f"subtitle-{production_id[:8]}.srt",
                )
            if subtitle_path and not validate_srt(subtitle_path):
                raise RuntimeError("字幕 SRT 校验失败，停止合成以免产出无字幕错误成片。")

            # 4b) 字幕末尾强制对齐真实音频（whisper 模式的尾部裁剪也走这里）。
            alignment: dict[str, Any] = {}
            if auto_align and subtitle_path and duration > 0:
                alignment["subtitle"] = align_srt_to_duration(subtitle_path, duration)

            # 5) 合成：复用 MPT 引擎（注入或惰性加载）。
            self._load_engine()
            params = self.build_video_params(language)
            combined = self.export_root / f"combined-{production_id[:8]}.mp4"
            self._combine_videos(
                combined_video_path=str(combined),
                video_paths=material_paths,
                audio_file=str(audio_final),
                video_aspect=params.video_aspect,
                video_concat_mode=params.video_concat_mode,
                max_clip_duration=params.video_clip_duration,
            )
            output = self.export_root / f"pet-{topic_id[:8] or 'topic'}-{language}-{production_id[:8]}.mp4"
            self._generate_video(
                video_path=str(combined),
                audio_path=str(audio_final),
                subtitle_path=subtitle_path,
                output_file=str(output),
                params=params,
                bgm_file_override=bgm_file or None,
            )
            if not output.exists() or output.stat().st_size == 0:
                raise RuntimeError("MPT 合成引擎未产出有效 MP4。")

            # 5b) MPT 为素材加了安全余量，成片通常比音频长；对齐到音频末尾。
            if auto_align:
                alignment["video"] = align_video_to_audio(
                    output, audio_final, tolerance=tolerance
                )
            alignment["verify"] = verify_alignment(
                audio_final, subtitle_path, output, tolerance=tolerance
            )
            if not alignment["verify"].get("aligned") and subtitle_path:
                logger.warning(
                    "时长对齐自检未通过 id=%s report=%s", production_id, alignment["verify"]
                )

            self._save(
                production_id, topic_id, task_id, language, "done",
                script_id="", tts_source=tts_result.get("source", ""), error="",
                audio_path=str(audio_final), subtitle_path=subtitle_path,
                material_paths=material_paths, output_path=str(output),
                alignment_report=json.dumps(alignment, ensure_ascii=False),
            )
            logger.info("视频生产完成 id=%s lang=%s file=%s", production_id, language, output.name)
            return {
                "production_id": production_id,
                "topic_id": topic_id,
                "language": language,
                "status": "done",
                "output_path": str(output),
                "audio_path": str(audio_final),
                "subtitle_path": subtitle_path,
                "materials": material_paths,
                "tts_source": tts_result.get("source", ""),
                "alignment": alignment,
            }
        except Exception as exc:
            self.guard.assert_no_fallback(Service.LOCAL_TTS) if self._is_paid_fallback(exc) else None
            self._save(production_id, topic_id, task_id, language, "failed",
                       script_id="", tts_source="", error=f"{type(exc).__name__}: {exc}")
            logger.error("视频生产失败 id=%s lang=%s: %s", production_id, language, exc)
            raise

    def _assert_no_ai_media(self) -> None:
        """付费 AI 图片/视频必须用户主动开启，且本模式永不调用它们。"""
        if self.config.is_enabled(Service.AI_IMAGE) or self.config.is_enabled(Service.AI_VIDEO):
            logger.warning(
                "检测到 AI 图片/视频服务已开启，但低成本模式 A 不会调用它们。"
            )

    @staticmethod
    def _is_paid_fallback(exc: Exception) -> bool:
        """识别是否意外试图走在线付费服务的错误（用于安全闸门留痕）。"""
        text = f"{type(exc).__name__} {exc}".lower()
        return any(k in text for k in ("edge-tts", "elevenlabs", "minimax", "azure tts", "openai tts"))

    @staticmethod
    def _audio_duration(audio_path: str) -> float:
        try:
            from app.services.voice import get_audio_duration

            value = get_audio_duration(audio_path)
            return float(value or 0)
        except Exception:
            logger.warning("无法读取音频时长，字幕将按每句固定时长兜底")
            return 0.0

    def _alignment_settings(self) -> tuple[bool, float]:
        """读取对齐开关与容差；配置缺失时用默认值。"""
        return (
            bool(self.config.get("video", "auto_align", True)),
            float(self.config.get("video", "align_tolerance", 0.3) or 0.3),
        )

    # ------------ 状态与重合成 ------------
    def _save(
        self,
        production_id: str,
        topic_id: str,
        task_id: str,
        language: str,
        status: str,
        *,
        script_id: str = "",
        tts_source: str = "",
        error: str = "",
        audio_path: str = "",
        subtitle_path: str = "",
        material_paths: list[str] | None = None,
        output_path: str = "",
        alignment_report: str = "",
    ) -> None:
        data: dict[str, Any] = {
            "production_id": production_id,
            "topic_id": topic_id,
            "task_id": task_id,
            "language": language,
            "status": status,
            "script_id": script_id,
            "tts_source": tts_source,
            "error": error,
            "updated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        }
        if audio_path:
            data["audio_path"] = audio_path
        if subtitle_path:
            data["subtitle_path"] = subtitle_path
        if material_paths is not None:
            data["material_paths"] = json.dumps(material_paths, ensure_ascii=False)
        if output_path:
            data["output_path"] = output_path
        if alignment_report:
            data["alignment_report"] = alignment_report
        existing = repo.get("productions", "production_id", production_id)
        if existing:
            repo.update("productions", "production_id", production_id, data)
        else:
            data.setdefault("created_at", data["updated_at"])
            repo.insert("productions", data)

    def get(self, production_id: str) -> dict[str, Any] | None:
        return repo.get("productions", "production_id", production_id)

    def list_productions(
        self, topic_id: str = "", language: str = "", status: str = ""
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for col, value in (("topic_id", topic_id), ("language", language), ("status", status)):
            if value:
                clauses.append(f"{col} = ?")
                params.append(value)
        where = " AND ".join(clauses) if clauses else ""
        rows = repo.list_rows(
            "productions",
            where=where,
            params=tuple(params),
            order_by="created_at DESC",
        )
        for row in rows:
            try:
                row["materials"] = json.loads(row.get("material_paths", "[]"))
            except (ValueError, TypeError):
                row["materials"] = []
            try:
                report = json.loads(row.get("alignment_report", "{}"))
                row["alignment"] = report if isinstance(report, dict) else {}
            except (ValueError, TypeError):
                row["alignment"] = {}
        return rows

    def produce_bilingual(
        self,
        topic_id: str,
        script_zh: str,
        script_en: str,
        materials_zh: list[dict[str, Any]] | None = None,
        materials_en: list[dict[str, Any]] | None = None,
        voice_file_zh: str | None = None,
        voice_file_en: str | None = None,
        audio_duration: float = 0.0,
        bgm_file: str = "",
        task_id: str = "",
        subtitles_mode: str = "script",
        storyboard_zh: list[dict[str, Any]] | None = None,
        storyboard_en: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """一个选题生成中英两条视频（任务书模块 8：双市场发布包）。

        两版独立合成、独立记录；英文版不复用作中文版的画面时间轴，
        字幕/配音都按英文脚本重新生成，避免机械对齐导致的错配。
        传入 storyboard 时，两版各自按分镜配素材、顺序拼接。
        """
        results: dict[str, Any] = {}
        for language, script, materials, voice_file, storyboard in (
            ("zh", script_zh, materials_zh, voice_file_zh, storyboard_zh),
            ("en", script_en, materials_en, voice_file_en, storyboard_en),
        ):
            if not script or not script.strip():
                raise ValueError(f"{language} 脚本为空，无法生成双语视频。")
            results[language] = self.produce(
                topic_id=topic_id,
                language=language,
                script=script,
                materials=materials,
                voice_file=voice_file,
                audio_duration=audio_duration,
                bgm_file=bgm_file,
                task_id=task_id,
                subtitles_mode=subtitles_mode,
                storyboard=storyboard,
            )
        return results

    def fix_alignment(self, production_id: str) -> dict[str, Any]:
        """校正已有成片的时长对齐：不重新合成，只修字幕时间轴与成片尾部。

        适用于早期生成的视频、或用户手动换过配音后时长错位的记录。
        """
        record = self.get(production_id)
        if not record:
            raise ValueError(f"生产记录不存在: {production_id}")
        audio = record.get("audio_path", "")
        if not audio or not Path(audio).is_file():
            raise RuntimeError("原配音文件不存在，无法按音频校正时长。")

        from PetAI.services.alignment_service import probe_duration

        duration = probe_duration(audio) or self._audio_duration(audio)
        if duration <= 0:
            raise RuntimeError("无法读取配音时长，校正中止（未改动原文件）。")
        tolerance = self._alignment_settings()[1]
        alignment: dict[str, Any] = {}
        subtitle = record.get("subtitle_path", "")
        if subtitle and Path(subtitle).is_file():
            alignment["subtitle"] = align_srt_to_duration(subtitle, duration)
        output = record.get("output_path", "")
        if output and Path(output).is_file():
            alignment["video"] = align_video_to_audio(
                output, audio, tolerance=tolerance
            )
            alignment["verify"] = verify_alignment(
                audio, subtitle, output, tolerance=tolerance
            )
        self._save(
            production_id, record.get("topic_id", ""), record.get("task_id", ""),
            record.get("language", "zh"), record.get("status", "done"),
            script_id=record.get("script_id", ""),
            tts_source=record.get("tts_source", ""),
            output_path=output,
            alignment_report=json.dumps(alignment, ensure_ascii=False),
        )
        result = self.get(production_id) or {}
        result["alignment"] = alignment
        logger.info("时长校正完成 id=%s report=%s", production_id, alignment)
        return result

    def recompose(self, production_id: str, bgm_file: str = "") -> dict[str, Any]:
        """重新合成：复用已生成的素材/音频/字幕，只重跑 MPT 合成步骤。"""
        record = self.get(production_id)
        if not record:
            raise ValueError(f"生产记录不存在: {production_id}")
        if not record.get("audio_path") or not Path(record["audio_path"]).is_file():
            raise RuntimeError("原配音文件不存在，无法重新合成；请使用完整 produce()。")
        materials = []
        try:
            materials = json.loads(record.get("material_paths", "[]"))
        except (ValueError, TypeError):
            pass
        if not materials:
            raise RuntimeError("原素材记录为空，无法重新合成。")
        # 兼容旧记录里保存的原始图片路径：重新合成前同样转成本地静帧视频。
        materials = [
            self._image_to_clip(Path(str(p)), production_id, i)
            if self.image_transcode
            and Path(str(p)).suffix.lower() in IMAGE_SUFFIXES
            and Path(str(p)).is_file()
            else str(p)
            for i, p in enumerate(materials)
        ]

        auto_align, tolerance = self._alignment_settings()
        duration = self._audio_duration(record["audio_path"])
        alignment: dict[str, Any] = {}
        subtitle_path = record.get("subtitle_path", "")
        if auto_align and subtitle_path and Path(subtitle_path).is_file() and duration > 0:
            alignment["subtitle"] = align_srt_to_duration(subtitle_path, duration)

        self._load_engine()
        params = self.build_video_params(record.get("language", "zh"))
        combined = self.export_root / f"recombined-{production_id[:8]}.mp4"
        self._combine_videos(
            combined_video_path=str(combined),
            video_paths=materials,
            audio_file=record["audio_path"],
            video_aspect=params.video_aspect,
            video_concat_mode=params.video_concat_mode,
            max_clip_duration=params.video_clip_duration,
        )
        output = record.get("output_path") or str(
            self.export_root / f"pet-recomposed-{production_id[:8]}.mp4"
        )
        self._generate_video(
            video_path=str(combined),
            audio_path=record["audio_path"],
            subtitle_path=subtitle_path,
            output_file=output,
            params=params,
            bgm_file_override=bgm_file or None,
        )
        if auto_align:
            alignment["video"] = align_video_to_audio(
                output, record["audio_path"], tolerance=tolerance
            )
        alignment["verify"] = verify_alignment(
            record["audio_path"], subtitle_path, output, tolerance=tolerance
        )
        self._save(
            production_id, record["topic_id"], record.get("task_id", ""),
            record.get("language", "zh"), "done", script_id=record.get("script_id", ""),
            tts_source=record.get("tts_source", ""), output_path=output,
            alignment_report=json.dumps(alignment, ensure_ascii=False),
        )
        result = self.get(production_id) or {}
        result["alignment"] = alignment
        return result
