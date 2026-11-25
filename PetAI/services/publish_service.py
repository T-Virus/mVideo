"""中英文双市场发布包（任务书模块 8）。

每个选题生成两套内容：中文发布包 + 英文发布包，
各含：视频、标题、简介、标签、封面文字。
第一版只生成发布文件，不开发自动发布功能。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from PetAI.config.settings import PETAI_ROOT
from PetAI.database import repository as repo
from PetAI.models.constants import LANG_EN, LANG_ZH
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("publish")


class PublishService:
    TABLE = "publish_packs"
    KEY = "pack_id"

    def __init__(self, config: Any, guard: CostGuard) -> None:
        self.config = config
        self.guard = guard
        self.export_root = PETAI_ROOT / "storage" / "exports"

    def create_pack(
        self,
        topic_id: str,
        script_id: str,
        video_path: str = "",
        task_id: str = "",
    ) -> dict[str, Any]:
        """按脚本生成一份发布包（语言随脚本），写入 JSON 文件，不自动发布。"""
        script = repo.get("scripts", "script_id", script_id)
        if not script:
            raise ValueError("脚本不存在")
        language = script["language"]
        if language not in (LANG_ZH, LANG_EN):
            raise ValueError("脚本语言无效")

        pack = {
            "pack_id": uuid.uuid4().hex,
            "topic_id": topic_id,
            "task_id": task_id,
            "language": language,
            "video_path": video_path,
            "title": script["title"],
            "description": script["description"],
            "hashtags": script["hashtags"],
            "thumbnail_text": self._thumbnail_text(script["title"]),
        }

        # 落地为发布文件（第一版只生成文件，不上传任何平台）
        self.export_root.mkdir(parents=True, exist_ok=True)
        file_path = self.export_root / f"{pack['pack_id']}-{language}.json"
        payload = {
            "language": language,
            "title": pack["title"],
            "description": pack["description"],
            "hashtags": repo.load_json(pack["hashtags"]),
            "thumbnail_text": pack["thumbnail_text"],
            "video_path": video_path,
            "topic_id": topic_id,
            "note": "发布文件，不自动发布到任何平台。",
        }
        tmp = file_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(file_path)
        pack["file_path"] = str(file_path)

        repo.insert(self.TABLE, pack)
        logger.info("发布包已生成 id=%s lang=%s file=%s", pack["pack_id"], language, file_path.name)
        return self.get(pack["pack_id"])

    def create_bilingual_packs(
        self,
        topic_id: str,
        zh_script_id: str,
        en_script_id: str,
        zh_video_path: str = "",
        en_video_path: str = "",
        task_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        """生成中英两套发布包。"""
        zh = self.create_pack(topic_id, zh_script_id, zh_video_path, task_id)
        en = self.create_pack(topic_id, en_script_id, en_video_path, task_id)
        return {"zh": zh, "en": en}

    def get(self, pack_id: str) -> dict[str, Any] | None:
        row = repo.get(self.TABLE, self.KEY, pack_id)
        return self._decode(row) if row else None

    def list(self, topic_id: str | None = None, language: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if topic_id:
            clauses.append("topic_id = ?")
            params.append(topic_id)
        if language:
            clauses.append("language = ?")
            params.append(language)
        rows = repo.list_rows(self.TABLE, where=" AND ".join(clauses), params=tuple(params), limit=limit)
        return [self._decode(r) for r in rows]

    @staticmethod
    def _thumbnail_text(title: str) -> str:
        """封面文字：标题取前 12 字（中文）/ 前 32 字符（英文），避免过长。"""
        title = title.strip()
        if not title:
            return ""
        is_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in title)
        return title[:12] if is_cjk else title[:32]

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row["hashtags"] = repo.load_json(row.get("hashtags"))
        return row
