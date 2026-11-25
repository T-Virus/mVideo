"""宠物知识库与审核中心（任务书模块 4）。

安全红线（逐条对应任务书 7 条审核规则）：
1. AI 不得自行编造参考文献 —— create()/ai_assist() 只接受用户录入来源；
   本服务从不自动生成 references 或 source_links。
2. 没有可靠来源时标记为待核查 —— 缺来源时 review_status 强制 pending。
3. 用药/剂量/疾病诊断/治疗方案必须人工复核 —— C 级强制 pending 且需人工。
4. 急症/中毒内容必须突出"及时联系当地兽医或动物急诊" —— 自动追加免责声明。
5. 不允许 AI 自动给评论区用户做个体诊断 —— 提供 answer_comment() 直接拒绝。
6. 未经人工审核的高风险内容不能标记为已批准 —— approve() 对 C 级强制要求审核人与备注。
7. 内容发布前保留人工确认环节 —— approve 需显式人工调用，AI 不能自批。

身份约束：创作者是兽医学习者，禁止自动使用执业兽医/专家等身份。
"""
from __future__ import annotations

import uuid
from typing import Any

from PetAI.database import repository as repo
from PetAI.models.constants import (
    EMERGENCY_DISCLAIMER,
    FORBIDDEN_IDENTITY_TERMS,
    IDENTITY_LEARNER,
    LANGUAGES,
    REVIEW_APPROVED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    RISK_A,
    RISK_B,
    RISK_B_KEYWORDS,
    RISK_C,
    RISK_C_KEYWORDS,
)
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("knowledge")


class KnowledgeService:
    TABLE = "knowledge"
    KEY = "knowledge_id"

    def __init__(
        self, config: Any, guard: CostGuard, deepseek: Any, doubao_scripts: Any = None
    ) -> None:
        self.config = config
        self.guard = guard
        self.deepseek = deepseek
        self.doubao_scripts = doubao_scripts

    @property
    def text_engine(self) -> str:
        """文本生成引擎：默认豆包桌面客户端；手动改配置才可用 deepseek。"""
        engine = str(
            self.config.get("script", "engine", "doubao-desktop") or "doubao-desktop"
        ).strip()
        return engine if engine in ("doubao-desktop", "deepseek") else "doubao-desktop"

    # ---------- 风险分级 ----------
    @staticmethod
    def classify_risk(topic: str, content: str = "") -> str:
        """按关键词自动分级：命中 C 关键词→C；否则命中 B→B；否则 A。只升不降由人工控制。"""
        text = f"{topic} {content}"
        if any(kw in text for kw in RISK_C_KEYWORDS):
            return RISK_C
        if any(kw in text for kw in RISK_B_KEYWORDS):
            return RISK_B
        return RISK_A

    @staticmethod
    def needs_emergency_disclaimer(text: str) -> bool:
        return any(kw in text for kw in ("急症", "急诊", "急救", "中毒", "误食", "呼吸困难", "抽搐"))

    @staticmethod
    def _has_reliable_source(references: list[str], source_links: list[str]) -> bool:
        return any(str(x).strip() for x in references) or any(
            str(x).strip() for x in source_links
        )

    # ---------- 创建 ----------
    def create(
        self,
        topic: str,
        content: str,
        animal_type: str = "both",
        category: str = "",
        references: list[str] | None = None,
        source_links: list[str] | None = None,
        published_date: str = "",
        verified_date: str = "",
        author_identity: str = IDENTITY_LEARNER,
        risk_level: str | None = None,
    ) -> dict[str, Any]:
        """录入知识。references/source_links 必须是用户提供的真实来源，AI 不得编造。"""
        if not topic.strip():
            raise ValueError("知识主题不能为空")
        if author_identity != IDENTITY_LEARNER:
            raise ValueError(
                "身份只能为兽医学习者(learner)，禁止自动标记为执业兽医或专家等不符合实际的资格"
            )

        references = [str(x) for x in (references or []) if str(x).strip()]
        source_links = [str(x) for x in (source_links or []) if str(x).strip()]
        # 自动分级（人工只能上调，见 set_risk_level）
        auto_risk = self.classify_risk(topic, content)
        level = risk_level or auto_risk
        if level not in (RISK_A, RISK_B, RISK_C):
            raise ValueError("risk_level 必须是 A/B/C 之一")
        if level == RISK_C and auto_risk != RISK_C:
            # 允许人工上调，但记录提示（保守起见允许，人工已介入）
            logger.info("人工上调风险等级至 C topic=%s", topic)

        # 规则 4：急症/中毒内容自动追加免责声明
        final_content = content.strip()
        if self.needs_emergency_disclaimer(f"{topic} {content}") and EMERGENCY_DISCLAIMER not in final_content:
            final_content = f"{final_content}\n\n{EMERGENCY_DISCLAIMER}"

        # 规则 2：无可靠来源 → 强制待核查
        has_source = self._has_reliable_source(references, source_links)
        review_status = REVIEW_PENDING
        record = {
            "knowledge_id": uuid.uuid4().hex,
            "topic": topic.strip(),
            "animal_type": animal_type,
            "category": category,
            "content": final_content,
            "references": repo.dump_json(references),
            "source_links": repo.dump_json(source_links),
            "published_date": published_date,
            "verified_date": verified_date,
            "risk_level": level,
            "review_status": review_status,
            "review_note": (
                "待核查：缺少可靠来源，请补充真实参考资料后再审核" if not has_source else ""
            ),
            "reviewed_by": "",
            "reviewed_at": "",
            "author_identity": IDENTITY_LEARNER,
        }
        repo.insert(self.TABLE, record)
        logger.info(
            "知识已录入 id=%s risk=%s source=%s review=%s",
            record["knowledge_id"], level, has_source, review_status,
        )
        return self.get(record["knowledge_id"])

    # ---------- 查询 ----------
    def get(self, knowledge_id: str) -> dict[str, Any] | None:
        row = repo.get(self.TABLE, self.KEY, knowledge_id)
        return self._decode(row) if row else None

    def list(
        self,
        animal_type: str | None = None,
        risk_level: str | None = None,
        review_status: str | None = None,
        keyword: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for col, val in (
            ("animal_type", animal_type),
            ("risk_level", risk_level),
            ("review_status", review_status),
        ):
            if val:
                clauses.append(f"{col} = ?")
                params.append(val)
        if keyword:
            clauses.append("(topic LIKE ? OR content LIKE ?)")
            params.extend((f"%{keyword}%", f"%{keyword}%"))
        rows = repo.list_rows(
            self.TABLE, where=" AND ".join(clauses), params=tuple(params), limit=limit
        )
        return [self._decode(r) for r in rows]

    # ---------- 审核（红线）----------
    def approve(self, knowledge_id: str, reviewer: str, note: str = "") -> dict[str, Any]:
        """人工批准。规则 3/6：C 级必须有审核人和备注；无来源不得批准。"""
        if not reviewer.strip():
            raise ValueError("批准必须记录审核人，AI 不能自动批准")
        row = self.get(knowledge_id)
        if not row:
            raise ValueError("知识不存在")
        if row["risk_level"] == RISK_C and not note.strip():
            raise ValueError("C 级高风险内容批准时必须填写审核备注")
        if not self._has_reliable_source(row["references"], row["source_links"]):
            raise ValueError("缺少可靠来源的知识不能批准，请先补充真实参考资料")
        from datetime import datetime

        repo.update(
            self.TABLE,
            self.KEY,
            knowledge_id,
            {
                "review_status": REVIEW_APPROVED,
                "reviewed_by": reviewer.strip(),
                "review_note": note.strip(),
                "reviewed_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        logger.info("知识已人工批准 id=%s reviewer=%s risk=%s", knowledge_id, reviewer, row["risk_level"])
        return self.get(knowledge_id)

    def reject(self, knowledge_id: str, reviewer: str, note: str) -> dict[str, Any]:
        if not reviewer.strip() or not note.strip():
            raise ValueError("驳回必须记录审核人和原因")
        from datetime import datetime

        repo.update(
            self.TABLE,
            self.KEY,
            knowledge_id,
            {
                "review_status": REVIEW_REJECTED,
                "reviewed_by": reviewer.strip(),
                "review_note": note.strip(),
                "reviewed_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        return self.get(knowledge_id)

    def set_risk_level(self, knowledge_id: str, level: str) -> dict[str, Any]:
        """人工调整风险等级。允许上调；下调到低于自动分级需写明理由（保守策略：直接拒绝下调）。"""
        if level not in (RISK_A, RISK_B, RISK_C):
            raise ValueError("risk_level 必须是 A/B/C 之一")
        row = self.get(knowledge_id)
        if not row:
            raise ValueError("知识不存在")
        auto = self.classify_risk(row["topic"], row["content"])
        order = {RISK_A: 0, RISK_B: 1, RISK_C: 2}
        if order[level] < order[auto]:
            raise ValueError(
                f"不能把含高风险关键词的内容从 {auto} 级下调到 {level} 级"
            )
        repo.update(self.TABLE, self.KEY, knowledge_id, {"risk_level": level})
        return self.get(knowledge_id)

    def can_publish(self, knowledge_id: str) -> bool:
        """发布前人工确认关卡：只有已人工批准的知识才可用于发布。"""
        row = self.get(knowledge_id)
        return bool(row and row["review_status"] == REVIEW_APPROVED)

    # ---------- 规则 5：禁止个体诊断 ----------
    def answer_comment(self, comment: str) -> str:
        """评论区的个体诊断请求一律拒绝，不调用 AI 生成诊断。"""
        return (
            "抱歉，我不能根据描述为具体宠物做出诊断或用药建议。"
            "如果你的宠物出现异常，请及时联系当地兽医或动物急诊。"
        )

    # ---------- AI 辅助（不编造来源）----------
    def ai_organize(self, knowledge_id: str, task_id: str = "") -> dict[str, Any]:
        """用本机豆包整理知识表述（免费）。仅改写已有正文，不生成任何参考文献。"""
        row = self.get(knowledge_id)
        if not row:
            raise ValueError("知识不存在")
        prompt = (
            "请整理并润色下面的宠物知识正文，使其清晰易懂、适合科普短视频口播。\n"
            "严格约束：\n"
            "- 只能基于给定正文改写，不得添加正文里没有的事实；\n"
            "- 禁止编造任何参考文献、来源、链接或研究结论；\n"
            "- 不得给出用药剂量、诊断或治疗方案；\n"
            "- 作者身份是兽医学习者，不要使用执业兽医/专家等身份表述；\n"
            "- 只输出改写后的正文，不要输出其他内容。\n\n"
            f"正文：{row['content']}"
        )
        if self.text_engine == "doubao-desktop":
            if self.doubao_scripts is None:
                raise RuntimeError("已选择豆包文本引擎，但未注入 DoubaoScriptService。")
            json_prompt = (
                prompt
                + '\n\n输出严格 JSON：{"content":"改写后的正文"}，不要输出 JSON 之外的文字。'
            )
            data = self.doubao_scripts.generate_json(json_prompt, task_id=task_id)
            organized = str(
                data.get("content") or data.get("text") or data.get("script") or ""
            ).strip()
            if not organized:
                raise RuntimeError("豆包未返回整理后的正文；不会改用付费服务")
        else:
            organized = self.deepseek.generate(prompt, task_id=task_id)
        organized = organized.strip()
        if not organized:
            raise RuntimeError("知识整理结果为空，已保留原正文")
        repo.update(self.TABLE, self.KEY, knowledge_id, {"content": organized})
        # 改写后仍保持待审核（AI 不能自批）
        repo.update(self.TABLE, self.KEY, knowledge_id, {"review_status": REVIEW_PENDING})
        return self.get(knowledge_id)

    # ---------- 内部 ----------
    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        row["references"] = repo.load_json(row.get("references"))
        row["source_links"] = repo.load_json(row.get("source_links"))
        return row
