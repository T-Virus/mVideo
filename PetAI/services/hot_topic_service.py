"""近 N 天同分类热门方向（豆包联网检索，本地落库为选题）。

边界说明：
- 豆包桌面版没有平台榜单接口，这里让它「联网检索 + 归纳趋势」，
  结果标注为参考建议，不宣称真实播放量，也不伪造数据。
- 取回失败会明确报错，不会改用付费服务。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from PetAI.models.constants import (
    ANIMAL_BOTH,
    ANIMAL_LABELS,
    CATEGORY_LABELS,
    CATEGORY_TO_ANIMAL,
    CATEGORY_TO_CONTENT_TYPE,
)
from PetAI.utils.safe_log import get_logger

logger = get_logger("hot")


class HotTopicService:
    def __init__(self, config: Any, topics: Any, text_engine: Any) -> None:
        self.config = config
        self.topics = topics
        self.engine = text_engine

    def discover(
        self,
        category: str,
        count: int | None = None,
        lookback_days: int | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        """按分类检索近 N 天热门方向，写入选题库并返回采用的第一条。"""
        animal = CATEGORY_TO_ANIMAL.get(category, ANIMAL_BOTH)
        label = CATEGORY_LABELS.get(category, category)
        days = int(lookback_days or self.config.get("hot", "lookback_days", 7) or 7)
        limit = max(1, int(count or self.config.get("hot", "plan_count", 3) or 3))
        wait = float(self.config.get("hot", "wait_seconds", 90) or 90)
        end = date.today()
        start = end - timedelta(days=days)

        prompt = (
            f"你是宠物短视频选题分析师。请联网检索 {start} 至 {end} 期间，"
            f"抖音/小红书/视频号上「{label}」类目播放量高的短视频，"
            f"归纳 {limit} 个可复制的选题方向。\n"
            f"内容面向{ANIMAL_LABELS.get(animal, '猫狗')}，"
            "不涉及用药剂量、诊断或治疗建议。\n"
            "每个方向只输出 4 个字段："
            "title(中文标题，口语化、有钩子)、"
            "why_hot(一句话说明最近为什么火)、"
            "hook(前 3 秒钩子)、"
            "keywords(3 个英文素材关键词数组)。\n"
            "如果拿不到实时榜单数据，data_source 写「模型趋势判断，非平台实时榜单」，"
            "并直接基于你了解的近期宠物内容趋势给出方向，不要留空。\n"
            '只输出 JSON：{"data_source":"...","topics":['
            '{"title":"","why_hot":"","hook":"","keywords":[]}]}，不要其他文字。'
        )
        data = self.engine.generate_json(prompt, task_id=task_id, wait_seconds=wait)
        items = data.get("topics") or data.get("plans") or []
        if not isinstance(items, list):
            items = []

        created: list[dict[str, Any]] = []
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            why_hot = str(item.get("why_hot") or "").strip()
            hook = str(item.get("hook") or "").strip()
            keywords = item.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [keywords]
            extra = "｜".join(
                part
                for part in (
                    why_hot,
                    f"钩子：{hook}" if hook else "",
                    f"素材：{', '.join(str(k) for k in keywords)}" if keywords else "",
                )
                if part
            )
            created.append(
                self.topics.create(
                    title=title,
                    animal_type=animal,
                    category=category,
                    description=extra,
                    target_audience=str(item.get("platform") or "短视频平台"),
                    content_type=CATEGORY_TO_CONTENT_TYPE.get(category),
                    source="doubao-hot",
                    task_id=task_id,
                )
            )
        if not created:
            raise RuntimeError(
                "豆包没有返回可用的热门选题（可能生成未完成或被折叠遮挡）。"
                "请重试，或调大 [hot].wait_seconds。系统不会伪造热门数据。"
            )
        logger.info(
            "热门选题完成 category=%s count=%d source=%s",
            category,
            len(created),
            data.get("data_source"),
        )
        return {
            "data_source": str(data.get("data_source") or "模型趋势判断，非平台实时榜单"),
            "items": created,
        }
