"""宠物选题中心（任务书模块 3）。

支持：手动新增、AI 批量生成、按猫/狗筛选、按科普/娱乐/用品筛选、
收藏、状态管理、关联素材。所有 AI 生成走 DeepSeek 闸门并记录成本。
"""
from __future__ import annotations

import uuid
from typing import Any

from PetAI.database import repository as repo
from PetAI.models.constants import (
    ANIMALS,
    CATEGORIES,
    CATEGORY_LABELS,
    CATEGORY_TO_ANIMAL,
    CATEGORY_TO_CONTENT_TYPE,
    CONTENT_TYPES,
    LANGUAGES,
    TOPIC_STATUSES,
    TOPIC_STATUS_IDEA,
)
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("topic")


class TopicService:
    TABLE = "topics"
    KEY = "topic_id"

    def __init__(self, config: Any, guard: CostGuard, deepseek: Any) -> None:
        self.config = config
        self.guard = guard
        self.deepseek = deepseek

    # ---------- 手动新增 ----------
    def create(
        self,
        title: str,
        animal_type: str = "both",
        category: str = "entertainment",
        description: str = "",
        target_audience: str = "",
        language: str = "zh",
        priority: int = 3,
        content_type: str | None = None,
        source: str = "manual",
        asset_ids: list[str] | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        if not title.strip():
            raise ValueError("选题标题不能为空")
        if animal_type not in ANIMALS:
            raise ValueError(f"animal_type 必须是 {ANIMALS} 之一")
        if category not in CATEGORIES:
            raise ValueError(f"category 必须是 {CATEGORIES} 之一")
        if language not in LANGUAGES:
            raise ValueError(f"language 必须是 {LANGUAGES} 之一")
        # 内容类型默认由分类推断，允许显式覆盖
        effective_content_type = content_type or CATEGORY_TO_CONTENT_TYPE.get(
            category, CONTENT_TYPES[1]
        )
        if effective_content_type not in CONTENT_TYPES:
            raise ValueError(f"content_type 必须是 {CONTENT_TYPES} 之一")

        record = {
            "topic_id": uuid.uuid4().hex,
            "title": title.strip(),
            "animal_type": animal_type,
            "category": category,
            "content_type": effective_content_type,
            "description": description,
            "target_audience": target_audience,
            "language": language,
            "priority": int(priority),
            "status": TOPIC_STATUS_IDEA,
            "favorite": 0,
            "source": source,
            "asset_ids": repo.dump_json(asset_ids or []),
            "task_id": task_id,
        }
        repo.insert(self.TABLE, record)
        logger.info("选题已创建 topic=%s title=%s", record["topic_id"], record["title"])
        return self.get(record["topic_id"])

    # ---------- 查询/筛选 ----------
    def get(self, topic_id: str) -> dict[str, Any] | None:
        row = repo.get(self.TABLE, self.KEY, topic_id)
        return self._decode(row) if row else None

    def list(
        self,
        animal_type: str | None = None,
        content_type: str | None = None,
        status: str | None = None,
        favorite_only: bool = False,
        category: str | None = None,
        language: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if animal_type:
            if animal_type not in ANIMALS:
                raise ValueError(f"animal_type 必须是 {ANIMALS} 之一")
            clauses.append("animal_type = ?")
            params.append(animal_type)
        if content_type:
            if content_type not in CONTENT_TYPES:
                raise ValueError(f"content_type 必须是 {CONTENT_TYPES} 之一")
            clauses.append("content_type = ?")
            params.append(content_type)
        if status:
            if status not in TOPIC_STATUSES:
                raise ValueError(f"status 必须是 {TOPIC_STATUSES} 之一")
            clauses.append("status = ?")
            params.append(status)
        if favorite_only:
            clauses.append("favorite = 1")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if language:
            clauses.append("language = ?")
            params.append(language)
        where = " AND ".join(clauses)
        rows = repo.list_rows(self.TABLE, where=where, params=tuple(params), limit=limit)
        return [self._decode(r) for r in rows]

    # ---------- 状态/收藏/关联 ----------
    def set_status(self, topic_id: str, status: str) -> dict[str, Any]:
        if status not in TOPIC_STATUSES:
            raise ValueError(f"status 必须是 {TOPIC_STATUSES} 之一")
        repo.update(self.TABLE, self.KEY, topic_id, {"status": status})
        return self.get(topic_id)

    def set_favorite(self, topic_id: str, favorite: bool) -> dict[str, Any]:
        repo.update(self.TABLE, self.KEY, topic_id, {"favorite": int(bool(favorite))})
        return self.get(topic_id)

    def attach_assets(self, topic_id: str, asset_ids: list[str]) -> dict[str, Any]:
        repo.update(self.TABLE, self.KEY, topic_id, {"asset_ids": repo.dump_json(asset_ids)})
        return self.get(topic_id)

    def delete(self, topic_id: str) -> int:
        return repo.delete(self.TABLE, self.KEY, topic_id)

    # ---------- AI 批量生成 ----------
    def generate_batch(
        self,
        count: int = 5,
        animal_type: str = "both",
        content_type: str | None = None,
        task_id: str = "",
    ) -> list[dict[str, Any]]:
        """用 DeepSeek 批量生成选题。失败时抛错，绝不切换到其他付费服务。"""
        if count < 1 or count > 30:
            raise ValueError("count 需在 1~30 之间")
        if animal_type not in ANIMALS:
            raise ValueError(f"animal_type 必须是 {ANIMALS} 之一")

        category_hint = ", ".join(f"{k}={v}" for k, v in CATEGORY_LABELS.items())
        type_hint = content_type or "混合（科普/娱乐/用品）"
        prompt = (
            "你是宠物内容选题助手。请生成 {n} 个短视频选题，面向{animal}内容，内容方向为{ctype}。\n"
            "可选分类及含义：{cats}。\n"
            "要求：标题口语化、有好奇心钩子；每条包含中文标题、一句话描述、目标受众、"
            "分类(category 用英文 key)、语言固定 zh。\n"
            "只输出严格 JSON：{{\"topics\":[{{\"title\":\"\",\"description\":\"\","
            "\"target_audience\":\"\",\"category\":\"\"}}]}}。\n"
            "不要输出 JSON 之外的任何文字。不要给出医疗诊断或用药建议。"
        ).format(
            n=count,
            animal={"cat": "猫咪", "dog": "狗狗", "both": "猫狗"}[animal_type],
            ctype=type_hint,
            cats=category_hint,
        )
        data = self.deepseek.generate_json(prompt, task_id=task_id)
        items = data.get("topics") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            raise RuntimeError("DeepSeek 未返回有效选题列表，请手动新增或重试")

        created: list[dict[str, Any]] = []
        for item in items[:count]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            category = str(item.get("category", "")).strip()
            if category not in CATEGORIES:
                category = "entertainment"
            # 尊重用户指定的动物类型；分类默认动物仅在 both 时采用
            effective_animal = animal_type
            if animal_type == "both":
                effective_animal = CATEGORY_TO_ANIMAL.get(category, "both")
            created.append(
                self.create(
                    title=title,
                    animal_type=effective_animal,
                    category=category,
                    description=str(item.get("description", "")),
                    target_audience=str(item.get("target_audience", "")),
                    language="zh",
                    content_type=(
                        content_type or CATEGORY_TO_CONTENT_TYPE.get(category, "entertainment")
                    ),
                    source="ai",
                    task_id=task_id,
                )
            )
        logger.info("AI 批量生成选题 %d 条", len(created))
        return created

    # ---------- 内部 ----------
    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row["asset_ids"] = repo.load_json(row.get("asset_ids"))
        row["favorite"] = bool(row.get("favorite"))
        return row
