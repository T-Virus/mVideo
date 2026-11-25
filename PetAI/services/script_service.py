"""双语脚本工厂（任务书模块 2/3/8）。

生成：中文/英文科普脚本、萌宠娱乐脚本、中译英本地化改写、分镜、
标题简介标签、素材搜索关键词。全部走 DeepSeek 闸门。

要点：
- 不允许机械逐字翻译，英文基于中文事实做本地化改写；
- 高风险知识未经人工批准不得写入脚本（发布前人工确认环节）；
- 作者身份固定为兽医学习者，禁止执业兽医等表述。
"""
from __future__ import annotations

import uuid
from typing import Any

from PetAI.database import repository as repo
from PetAI.models.constants import (
    EMERGENCY_DISCLAIMER,
    FORBIDDEN_IDENTITY_TERMS,
    LANG_EN,
    LANG_ZH,
    REVIEW_APPROVED,
    REVIEW_PENDING,
    RISK_C,
)
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("script")


class ScriptService:
    TABLE = "scripts"
    KEY = "script_id"

    def __init__(
        self,
        config: Any,
        guard: CostGuard,
        deepseek: Any,
        knowledge_service: Any,
    ) -> None:
        self.config = config
        self.guard = guard
        self.deepseek = deepseek
        self.knowledge = knowledge_service

    # ---------- 脚本生成 ----------
    def generate(
        self,
        topic_id: str,
        language: str,
        script_type: str = "science",
        knowledge_ids: list[str] | None = None,
        task_id: str = "",
        duration_seconds: int = 45,
    ) -> dict[str, Any]:
        """为选题生成单语脚本 + 分镜 + 标题简介标签 + 素材关键词。"""
        if language not in (LANG_ZH, LANG_EN):
            raise ValueError("language 必须是 zh 或 en")

        knowledge_ids = knowledge_ids or []
        # 安全关卡：引用了 C 级知识时，必须已人工批准
        for kid in knowledge_ids:
            k = self.knowledge.get(kid)
            if not k:
                raise ValueError(f"引用的知识不存在: {kid}")
            if k["risk_level"] == RISK_C and k["review_status"] != REVIEW_APPROVED:
                raise ValueError(
                    f"知识 {kid} 为 C 级高风险且未经人工批准，不能写入脚本。"
                    f"请先在知识库中人工审核通过。"
                )

        # 收集可用事实（只来自用户录入且已批准的知识，AI 不编造来源）
        facts = self._collect_facts(knowledge_ids)

        lang_instruction = (
            "用简体中文写作，口播自然、有钩子。"
            if language == LANG_ZH
            else "Write in natural, idiomatic English for a short-video voiceover. "
                 "Do NOT translate word-by-word from Chinese; localize the expression "
                 "for an English-speaking audience while keeping the same facts."
        )
        type_instruction = (
            "科普知识内容，严谨但易懂。"
            if script_type == "science"
            else "轻松萌宠娱乐内容，有趣、可爱、无医疗建议。"
        )
        duration_note = f"口播时长约 {duration_seconds} 秒（约 {duration_seconds * 4} 字/词量级）。"
        facts_block = "\n".join(f"- {f}" for f in facts) if facts else "（无引用知识，仅基于选题标题创作，不得声称事实来源）"

        prompt = (
            "你是宠物短视频脚本编剧。基于给定选题创作一条脚本。\n"
            f"选题：{self._topic_title(topic_id)}\n"
            f"脚本类型：{type_instruction}\n"
            f"语言要求：{lang_instruction}\n"
            f"时长：{duration_note}\n"
            f"可用事实（只能使用这些事实，不得虚构病例或数据）：\n{facts_block}\n\n"
            "严格约束：\n"
            "- 作者身份是兽医学习者，禁止使用执业兽医/兽医专家等身份表述；\n"
            "- 不得给出用药剂量、诊断或治疗方案；\n"
            "- 涉及急症/中毒时提醒联系当地兽医或动物急诊；\n"
            "- 不得编造参考文献、来源或临床病例；\n"
            "- 输出 JSON，字段：script(正文)、storyboard(分镜数组，每项含"
            "scene/description/asset_keyword)、title、description、hashtags(数组)、"
            "asset_keywords(数组)。\n"
            "只输出 JSON，不要其他文字。"
        )
        data = self.deepseek.generate_json(prompt, task_id=task_id)
        if not isinstance(data, dict):
            raise RuntimeError("DeepSeek 未返回有效脚本 JSON")

        storyboard = data.get("storyboard", [])
        if not isinstance(storyboard, list):
            storyboard = []
        content = str(data.get("script", "")).strip()
        if not content:
            raise RuntimeError("DeepSeek 返回的脚本正文为空")

        # 急症内容确保带免责声明
        if self._needs_disclaimer(content) and EMERGENCY_DISCLAIMER not in content and language == LANG_ZH:
            content += f"\n\n{EMERGENCY_DISCLAIMER}"

        record = {
            "script_id": uuid.uuid4().hex,
            "topic_id": topic_id,
            "task_id": task_id,
            "language": language,
            "script_type": script_type,
            "content": content,
            "storyboard": repo.dump_json(storyboard),
            "title": str(data.get("title", "")),
            "description": str(data.get("description", "")),
            "hashtags": repo.dump_json(self._as_list(data.get("hashtags"))),
            "asset_keywords": repo.dump_json(self._as_list(data.get("asset_keywords"))),
            "knowledge_ids": repo.dump_json(knowledge_ids),
            "review_status": REVIEW_PENDING,
        }
        repo.insert(self.TABLE, record)
        logger.info("脚本已生成 id=%s lang=%s type=%s", record["script_id"], language, script_type)
        return self.get(record["script_id"])

    def generate_bilingual(
        self,
        topic_id: str,
        script_type: str = "science",
        knowledge_ids: list[str] | None = None,
        task_id: str = "",
    ) -> dict[str, dict[str, Any]]:
        """生成中英两条脚本。英文基于同一批事实本地化改写，而非逐字翻译中文脚本文本。"""
        zh = self.generate(
            topic_id, LANG_ZH, script_type, knowledge_ids, task_id=task_id
        )
        en = self.generate(
            topic_id, LANG_EN, script_type, knowledge_ids, task_id=task_id
        )
        return {"zh": zh, "en": en}

    def localize_to_english(self, script_id: str, task_id: str = "") -> dict[str, Any]:
        """把已有中文脚本本地化为英文（不是机械翻译）。"""
        src = repo.get(self.TABLE, self.KEY, script_id)  # 取原始 JSON 字符串，避免重复编码
        if not src:
            raise ValueError("脚本不存在")
        if src["language"] != LANG_ZH:
            raise ValueError("只能本地化中文脚本")
        prompt = (
            "Rewrite the following Chinese pet-video script into natural English for an "
            "English-speaking audience. Do NOT translate word-by-word; adapt the expression "
            "while preserving the exact facts. Keep the same structure. "
            "Do not invent facts, references or medical advice. "
            "The author is a veterinary learner, never claim to be a licensed veterinarian. "
            "Return JSON: script, title, description, hashtags(array).\n\n"
            f"Chinese script:\n{src['content']}"
        )
        data = self.deepseek.generate_json(prompt, task_id=task_id)
        if not isinstance(data, dict):
            raise RuntimeError("DeepSeek 未返回有效本地化结果")
        record = {
            "script_id": uuid.uuid4().hex,
            "topic_id": src["topic_id"],
            "task_id": task_id,
            "language": LANG_EN,
            "script_type": src["script_type"],
            "content": str(data.get("script", "")).strip(),
            "storyboard": src["storyboard"],
            "title": str(data.get("title", "")),
            "description": str(data.get("description", "")),
            "hashtags": repo.dump_json(self._as_list(data.get("hashtags"))),
            "asset_keywords": src["asset_keywords"],
            "knowledge_ids": src["knowledge_ids"],
            "review_status": REVIEW_PENDING,
        }
        repo.insert(self.TABLE, record)
        return self.get(record["script_id"])

    # ---------- 查询 ----------
    def get(self, script_id: str) -> dict[str, Any] | None:
        row = repo.get(self.TABLE, self.KEY, script_id)
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

    # ---------- 内部 ----------
    def _topic_title(self, topic_id: str) -> str:
        row = repo.get("topics", "topic_id", topic_id)
        return row["title"] if row else "（未找到选题标题）"

    def _collect_facts(self, knowledge_ids: list[str]) -> list[str]:
        facts = []
        for kid in knowledge_ids:
            k = self.knowledge.get(kid)
            if k and k["review_status"] == REVIEW_APPROVED:
                # 只使用已人工批准的知识正文作为事实来源
                facts.append(k["content"])
        return facts

    @staticmethod
    def _needs_disclaimer(text: str) -> bool:
        return any(kw in text for kw in ("急症", "急诊", "急救", "中毒", "误食", "呼吸困难", "抽搐"))

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(x) for x in value]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for field in ("storyboard", "hashtags", "asset_keywords", "knowledge_ids"):
            row[field] = repo.load_json(row.get(field))
        return row
