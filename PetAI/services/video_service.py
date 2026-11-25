"""视频生产中心（任务书模块 7/8）。

设计原则：
1. 复用 MoneyPrinterTurbo 现有合成引擎（combine_videos + generate_video），
   不改任何 MPT 核心代码；合成函数通过构造参数注入，便于离线测试。
2. 默认模式 A（低成本宠物素材）：本地素材 + 本地配音 + 本地字幕 + FFmpeg 合成。
   绝不调用 AI 图片/视频生成服务。
3. 固定 9:16 竖屏（VideoAspect.portrait → 1080×1920）。
4. 中英文独立成片，各自记录生产状态，支持重新合成。

合成链路由 MPT 提供：
    MPT combine_videos(无声素材视频) -> MPT generate_video(画面+音频+字幕 -> MP4)
PetAI 只负责：选素材、本地配音、本地字幕、组装参数、落库与产物管理。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from PetAI.config.settings import PETAI_ROOT, Service
from PetAI.database import repository as repo
from PetAI.services.cost_service import CostGuard
from PetAI.services.local_asset_service import LocalAssetService
from PetAI.services.subtitle_service import SubtitleService, validate_srt
from PetAI.services.tts_service import TTSService
from PetAI.utils.safe_log import get_logger

logger = get_logger("video")

Status = str  # pending / running / done / failed


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

    # ---------- MPT 引擎（惰性解析，不改其核心） ----------
    def _load_engine(self) -> None:
        if self._combine_videos is None or self._generate_video is None:
            from app.services import video as mpt_video

            self._combine_videos = self._combine_videos or mpt_video.combine_videos
            self._generate_video = self._generate_video or mpt_video.generate_video

    def build_video_params(self, language: str = "zh") -> Any:
        """构造 MPT VideoParams：固定 9:16 竖屏 + 本地字幕字体。"""
        from app.models.schema import VideoAspect, VideoParams

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
            video_clip_duration=int(self.config.get("video", "clip_duration", 5)),
            subtitle_enabled=subtitle_enabled,
            font_name=str(self.config.get("video", "font_name", "STHeitiMedium.ttc")),
            bgm_type=str(self.config.get("video", "bgm_type", "random")),
            voice_name="",                             # 配音已由 PetAI 本地生成
        )

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
    ) -> dict[str, Any]:
        """生产一条视频。默认走低成本模式 A。

        materials: 本地素材条目（含 path/kind）；为空时从本地素材库按标签优先选取。
        subtitles_mode=none 时不生成字幕（subtitle_path 传空串给 MPT）。
        """
        self.guard.require(Service.LOCAL_TTS)  # 本地配音闸门
        self._assert_no_ai_media()             # 再次确认未启用付费 AI 画面

        production_id = uuid.uuid4().hex
        self._save(production_id, topic_id, task_id, language, "running",
                   script_id="", tts_source="", error="")
        try:
            # 1) 素材：本地优先，绝不自动下载网络素材。
            selected = materials or self.assets.select_local_first(animal="both")
            if not selected:
                raise RuntimeError(
                    "本地素材库为空。请先上传猫咪/狗狗素材，系统不会自动下载网络素材。"
                )
            material_paths = [str(m["path"]) for m in selected if Path(m.get("path", "")).is_file()]
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

            # 3) 本地时长（供素材切分与字幕分配）。MPT 会以自身逻辑再取一次时长。
            duration = audio_duration or self._audio_duration(audio_final)

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

            self._save(
                production_id, topic_id, task_id, language, "done",
                script_id="", tts_source=tts_result.get("source", ""), error="",
                audio_path=str(audio_final), subtitle_path=subtitle_path,
                material_paths=material_paths, output_path=str(output),
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
    ) -> dict[str, Any]:
        """一个选题生成中英两条视频（任务书模块 8：双市场发布包）。

        两版独立合成、独立记录；英文版不复用作中文版的画面时间轴，
        字幕/配音都按英文脚本重新生成，避免机械对齐导致的错配。
        """
        results: dict[str, Any] = {}
        for language, script, materials, voice_file in (
            ("zh", script_zh, materials_zh, voice_file_zh),
            ("en", script_en, materials_en, voice_file_en),
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
            )
        return results

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
            subtitle_path=record.get("subtitle_path", ""),
            output_file=output,
            params=params,
            bgm_file_override=bgm_file or None,
        )
        self._save(
            production_id, record["topic_id"], record.get("task_id", ""),
            record.get("language", "zh"), "done", script_id=record.get("script_id", ""),
            tts_source=record.get("tts_source", ""), output_path=output,
        )
        return self.get(production_id) or {}
