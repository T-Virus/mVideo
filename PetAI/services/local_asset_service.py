"""本地素材服务：本地优先、上传不覆盖、分类与授权留痕。

对应任务书模块 5（阶段 2 先实现底层能力）。网络素材下载默认关闭，
且必须记录来源与授权信息；本服务不抓取任何他人视频。
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from PetAI.config.settings import PETAI_ROOT, Service
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("asset")

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}

CATEGORY_DIRS = {
    "cat_video": "cats",
    "dog_video": "dogs",
    "cat_image": "cats",
    "dog_image": "dogs",
    "educational": "educational",
    "audio": "audio",
    "video": "videos",
    "export": "exports",
}


class LocalAssetService:
    def __init__(self, config: Any, guard: CostGuard, storage_root: str | Path | None = None) -> None:
        self.config = config
        self.guard = guard
        self.storage_root = Path(storage_root) if storage_root else (PETAI_ROOT / "storage")
        self.index_path = self.storage_root / "asset_index.json"

    # ---------- 分类与目录 ----------
    def category_dir(self, category: str) -> Path:
        sub = CATEGORY_DIRS.get(category, "educational")
        path = self.storage_root / sub
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"assets": []}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"assets": []}
        except (json.JSONDecodeError, OSError):
            logger.warning("素材索引损坏，重建空索引")
            return {"assets": []}

    def _save_index(self, data: dict[str, Any]) -> None:
        tmp = self.index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    # ---------- 上传（绝不覆盖原始素材） ----------
    def upload(
        self,
        src_path: str | Path,
        category: str,
        tags: list[str] | None = None,
        license_info: str = "用户自有/已授权",
        source: str = "local-upload",
    ) -> dict[str, Any]:
        """上传素材：同名不覆盖，自动追加唯一后缀。原文件永不删除或修改。"""
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError(f"素材文件不存在: {src}")
        target_dir = self.category_dir(category)
        suffix = src.suffix.lower()
        stem = src.stem
        target = target_dir / f"{stem}{suffix}"
        if target.exists():
            # 不覆盖：生成新文件名
            target = target_dir / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"
        shutil.copy2(str(src), str(target))  # 复制而非移动，保留原文件

        asset = {
            "asset_id": uuid.uuid4().hex,
            "filename": target.name,
            "category": category,
            "kind": self.detect_kind(suffix),
            "path": str(target),
            "original_path": str(src),
            "tags": tags or [],
            "license": license_info,
            "source": source,
            "usage_count": 0,
            "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        }
        index = self._load_index()
        index.setdefault("assets", []).append(asset)
        self._save_index(index)
        logger.info("素材已入库(未覆盖原文件) category=%s file=%s", category, target.name)
        return asset

    @staticmethod
    def detect_kind(extension: str) -> str:
        ext = extension.lower()
        if ext in VIDEO_EXTS:
            return "video"
        if ext in IMAGE_EXTS:
            return "image"
        if ext in AUDIO_EXTS:
            return "audio"
        return "other"

    # ---------- 查询 ----------
    def list_assets(
        self,
        category: str | None = None,
        animal: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        assets = list(self._load_index().get("assets", []))
        if category:
            assets = [a for a in assets if a.get("category") == category]
        if animal:
            # animal: cat / dog / both，按目录与标签匹配
            assets = [
                a
                for a in assets
                if animal in str(a.get("category", ""))
                or animal in a.get("tags", [])
            ]
        if keyword:
            kw = keyword.lower()
            assets = [
                a
                for a in assets
                if kw in a.get("filename", "").lower()
                or any(kw in t.lower() for t in a.get("tags", []))
            ]
        return assets

    def mark_used(self, asset_id: str) -> None:
        """累计素材使用次数。"""
        index = self._load_index()
        for asset in index.get("assets", []):
            if asset.get("asset_id") == asset_id:
                asset["usage_count"] = int(asset.get("usage_count", 0)) + 1
                break
        self._save_index(index)

    # ---------- 本地优先选取 ----------
    def select_local_first(
        self,
        wanted_tags: list[str] | None = None,
        kind: str | None = None,
        count: int = 1,
        animal: str | None = None,
    ) -> list[dict[str, Any]]:
        """按本地素材优先策略挑选画面。

        若 local_asset_first 关闭，仍返回本地素材，但显式记录告警；
        本服务永不自动下载网络素材（需用户主动开启 network_asset 且手动触发）。
        """
        if not self.config.get("services", "local_asset_first", True):
            logger.warning("本地素材优先已关闭，但 PetAI 不会自动下载网络素材")
        candidates = self.list_assets(animal=animal)
        if kind:
            candidates = [a for a in candidates if a.get("kind") == kind]
        if wanted_tags:
            def score(asset: dict[str, Any]) -> int:
                tags = {t.lower() for t in asset.get("tags", [])}
                return sum(1 for t in wanted_tags if t.lower() in tags)

            candidates.sort(key=score, reverse=True)
        selected = candidates[:count]
        for asset in selected:
            self.mark_used(asset["asset_id"])
        logger.info("本地优先选取 %d 个素材（候选 %d 个）", len(selected), len(candidates))
        return selected

    # ---------- 按分镜配素材 ----------
    def plan_for_storyboard(
        self,
        storyboard: list[dict[str, Any]],
        animal: str | None = None,
    ) -> list[dict[str, Any]]:
        """按分镜顺序为每个镜头配一个素材。

        优先用 asset_keyword 与 tags/文件名匹配；匹配不到就用同类素材轮换。
        素材不足时允许重复，但尽量错开，避免整条视频只有同一个画面。
        """
        usable = [
            asset
            for asset in self.list_assets(animal=animal)
            if asset.get("kind") in ("video", "image")
            and Path(asset.get("path", "")).is_file()
        ]
        if not usable:
            return []

        plan: list[dict[str, Any]] = []
        used: set[str] = set()
        for index, scene in enumerate(storyboard or []):
            if not isinstance(scene, dict):
                continue
            keyword = str(scene.get("asset_keyword") or "").strip()
            best = self._best_asset(usable, keyword, used)
            if best is None:
                best = self._next_unused(usable, used) or usable[index % len(usable)]
            score = self._match_score(best, keyword) if keyword else 0
            plan.append(
                {
                    "path": best["path"],
                    "kind": best.get("kind", "video"),
                    "asset_id": best.get("asset_id", ""),
                    "scene": str(scene.get("scene") or index + 1),
                    "keyword": keyword,
                    "matched": score > 0,
                }
            )
            used.add(best["path"])
        for item in plan:
            if item.get("asset_id"):
                self.mark_used(item["asset_id"])
        logger.info(
            "按分镜配素材：%d 个镜头，本地可用素材 %d 个", len(plan), len(usable)
        )
        return plan

    # 这些词几乎所有宠物素材都有，不能作为"精准匹配"依据，否则每张都算命中。
    _GENERIC_WORDS = {
        "cat", "cats", "dog", "dogs", "pet", "pets", "animal", "animals",
        "video", "image", "photo", "clip", "footage", "猫咪", "狗狗", "宠物",
    }

    @classmethod
    def _match_score(cls, asset: dict[str, Any], keyword: str) -> int:
        words = [
            word
            for word in re.split(r"[\s,;/]+", keyword.lower())
            if len(word) > 1 and word not in cls._GENERIC_WORDS
        ]
        if not words:
            return 0
        haystack = " ".join(
            [
                str(asset.get("filename", "")),
                " ".join(str(t) for t in asset.get("tags", [])),
                str(asset.get("category", "")),
            ]
        ).lower()
        return sum(1 for word in words if word in haystack)

    def _best_asset(
        self, assets: list[dict[str, Any]], keyword: str, used: set[str]
    ) -> dict[str, Any] | None:
        if not keyword:
            return None
        scored: list[tuple[int, dict[str, Any]]] = []
        for asset in assets:
            score = self._match_score(asset, keyword)
            if score > 0:
                scored.append((score, asset))
        if not scored:
            return None
        # 同分时优先没用过的、使用次数少的，避免整条视频反复用同一张。
        scored.sort(
            key=lambda pair: (
                pair[0],
                pair[1]["path"] not in used,
                -int(pair[1].get("usage_count", 0) or 0),
            ),
            reverse=True,
        )
        return scored[0][1]

    @staticmethod
    def _next_unused(
        assets: list[dict[str, Any]], used: set[str]
    ) -> dict[str, Any] | None:
        unused = [asset for asset in assets if asset["path"] not in used]
        if not unused:
            return None
        return min(unused, key=lambda a: int(a.get("usage_count", 0) or 0))

    # ---------- 网络素材闸门 ----------
    def assert_network_asset_allowed(self, license_info: str = "") -> None:
        """网络素材下载必须用户主动开启，且必须有授权信息。"""
        self.guard.require(Service.NETWORK_ASSET)
        if not license_info.strip():
            raise ValueError("网络素材必须提供来源与授权信息，禁止未授权抓取。")
