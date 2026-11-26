"""一键生产编排：热门选题 → 中英脚本 → 分镜素材 → 配音合成 → 发布包。

设计边界：
- 用户只选分类，其余全自动；每一步通过 progress 回调报告进度。
- 豆包素材生成失败会回退本地素材；本地也没有则明确报错，不静默产出空视频。
- 脚本命中 C 级高风险内容立即停止，交人工审核（安全红线不可绕过）。
"""
from __future__ import annotations

from typing import Any, Callable

from PetAI.models.constants import (
    CATEGORY_LABELS,
    CATEGORY_TO_ANIMAL,
    LANG_EN,
    LANG_ZH,
    RISK_C,
)
from PetAI.utils.safe_log import get_logger

logger = get_logger("pipeline")

ProgressFn = Callable[[int, int, str], None]

STEPS = (
    "检索近 7 天热门方向",
    "生成中英脚本与分镜",
    "内容风险检查",
    "按分镜准备素材",
    "本地配音与合成",
    "生成发布包",
)


class PipelineService:
    def __init__(
        self,
        config: Any,
        hot: Any,
        scripts: Any,
        knowledge: Any,
        doubao: Any,
        assets: Any,
        video: Any,
        publish: Any,
    ) -> None:
        self.config = config
        self.hot = hot
        self.scripts = scripts
        self.knowledge = knowledge
        self.doubao = doubao
        self.assets = assets
        self.video = video
        self.publish = publish

    def run(
        self,
        category: str,
        image_count: int | None = None,
        with_english: bool | None = None,
        task_id: str = "",
        progress: ProgressFn | None = None,
    ) -> dict[str, Any]:
        """跑完整流程；任何一步失败都会抛出带步骤上下文的错误。"""
        total = len(STEPS)

        def emit(step: int, message: str) -> None:
            if callable(progress):
                progress(step, total, message)

        image_count = int(
            image_count or self.config.get("pipeline", "image_count", 6) or 6
        )
        if with_english is None:
            with_english = bool(self.config.get("pipeline", "with_english", True))

        # 1) 热门选题
        emit(1, f"{STEPS[0]}（豆包联网检索，约 1 分钟）…")
        try:
            found = self.hot.discover(category, task_id=task_id)
        except Exception as exc:
            raise RuntimeError(f"第 1 步失败（热门选题）：{exc}") from exc
        topic = found["items"][0]

        # 2) 中英脚本 + 分镜
        emit(2, f"{STEPS[1]}（中英各约 25 秒）…")

        def generate_for(candidate: dict[str, Any]) -> tuple[Any, Any]:
            if with_english:
                pair = self.scripts.generate_bilingual(
                    candidate["topic_id"], task_id=task_id
                )
                return pair[LANG_ZH], pair[LANG_EN]
            return (
                self.scripts.generate(candidate["topic_id"], LANG_ZH, task_id=task_id),
                None,
            )

        def risk_of(candidate: dict[str, Any], script: dict[str, Any]) -> str:
            return self.knowledge.classify_risk(
                str(candidate.get("title") or ""), str(script.get("content") or "")
            )

        try:
            zh, en = generate_for(topic)
        except Exception as exc:
            raise RuntimeError(f"第 2 步失败（脚本生成）：{exc}") from exc

        # 3) 风险检查：C 级先自动换候选选题，仍不行才停（安全红线不绕过）
        emit(3, f"{STEPS[2]}…")
        risk = risk_of(topic, zh)
        switched = False
        if risk == RISK_C:
            alternatives = [
                item
                for item in found["items"][1:]
                if item.get("topic_id") != topic.get("topic_id")
            ]
            for alt in alternatives:
                logger.warning(
                    "选题命中 C 级，自动换候选：%s -> %s",
                    topic.get("title"),
                    alt.get("title"),
                )
                try:
                    alt_zh, alt_en = generate_for(alt)
                except Exception as exc:  # noqa: BLE001 - 备选失败就试下一个
                    logger.warning("备选选题脚本生成失败：%s", exc)
                    continue
                alt_risk = risk_of(alt, alt_zh)
                if alt_risk != RISK_C:
                    topic, zh, en, risk = alt, alt_zh, alt_en, alt_risk
                    switched = True
                    break
        if risk == RISK_C:
            raise RuntimeError(
                "第 3 步拦截：候选选题均命中 C 级高风险内容（用药/剂量/急症等），"
                "已自动尝试其他候选仍未通过。建议改选「萌宠娱乐」或「宠物用品与生活」分类，"
                "或到「知识库与审核」人工核查。"
            )

        # 4) 按分镜准备素材：豆包优先，失败回退本地
        storyboard_zh = zh.get("storyboard") or []
        animal = CATEGORY_TO_ANIMAL.get(category, "both")
        emit(
            4,
            f"{STEPS[3]}（{len(storyboard_zh)} 个镜头，每张约 40 秒）…",
        )
        material_source = "doubao"
        try:
            materials = self.doubao.generate_for_storyboard(
                storyboard_zh, animal=animal, max_images=image_count
            )
        except Exception as exc:  # noqa: BLE001 - 失败要回退而不是中断
            logger.warning("豆包按分镜生成失败，回退本地素材：%s", exc)
            materials = self.assets.plan_for_storyboard(storyboard_zh)
            material_source = "local-fallback"
            if not materials:
                raise RuntimeError(
                    f"第 4 步失败（素材）：豆包生成失败（{exc}），"
                    "本地素材库也没有可用素材。请先上传素材或重试。"
                ) from exc

        # 5) 配音 + 合成
        emit(5, f"{STEPS[4]}（本地 SAPI + FFmpeg，约 1 分钟）…")
        storyboard_en = (en or {}).get("storyboard") or storyboard_zh
        try:
            if with_english and en:
                videos = self.video.produce_bilingual(
                    topic_id=topic["topic_id"],
                    script_zh=zh["content"],
                    script_en=en["content"],
                    materials_zh=materials,
                    materials_en=materials,
                    storyboard_zh=storyboard_zh,
                    storyboard_en=storyboard_en,
                    audio_duration=0.0,
                    task_id=task_id,
                )
            else:
                videos = {
                    LANG_ZH: self.video.produce(
                        topic_id=topic["topic_id"],
                        language=LANG_ZH,
                        script=zh["content"],
                        materials=materials,
                        storyboard=storyboard_zh,
                        audio_duration=0.0,
                        task_id=task_id,
                    )
                }
        except Exception as exc:
            raise RuntimeError(f"第 5 步失败（配音/合成）：{exc}") from exc

        # 6) 发布包（失败不影响成片）
        emit(6, f"{STEPS[5]}…")
        packs: dict[str, Any] = {}
        try:
            if with_english and en:
                packs = self.publish.create_bilingual_packs(
                    topic["topic_id"],
                    zh["script_id"],
                    en["script_id"],
                    zh_video_path=videos.get(LANG_ZH, {}).get("output_path", ""),
                    en_video_path=videos.get(LANG_EN, {}).get("output_path", ""),
                    task_id=task_id,
                )
            else:
                packs = {
                    LANG_ZH: self.publish.create_pack(
                        topic["topic_id"],
                        zh["script_id"],
                        videos.get(LANG_ZH, {}).get("output_path", ""),
                        task_id=task_id,
                    )
                }
        except Exception as exc:  # noqa: BLE001 - 发布包不是硬失败
            logger.warning("发布包生成失败（成片已保留）：%s", exc)

        logger.info(
            "一键生产完成 category=%s topic=%s material=%s",
            category,
            topic.get("topic_id"),
            material_source,
        )
        return {
            "category": category,
            "category_label": CATEGORY_LABELS.get(category, category),
            "data_source": found.get("data_source", ""),
            "topic": topic,
            "scripts": {LANG_ZH: zh, LANG_EN: en},
            "risk": risk,
            "topic_switched": switched,
            "materials": materials,
            "material_source": material_source,
            "videos": videos,
            "packs": packs,
        }
