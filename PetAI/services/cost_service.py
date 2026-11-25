"""成本控制中心 + 付费服务闸门。

对应任务书模块 1。这是所有付费调用的**唯一强制入口**：

- 任何付费服务在发起真实网络请求前，必须先通过 CostGuard.require()；
- 服务在配置中关闭时，require() 抛出 ServiceDisabledError，调用被阻断；
- 不存在任何"主服务失败自动切换其他付费服务"的回退逻辑；
- DeepSeek 调用记录模型、时间、Token、任务 ID；
- 价格来自可配置价格表，Token 缺失时标记为估算，绝不伪装精确账单。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from PetAI.config.settings import PAID_SERVICES, Service, mask_secret
from PetAI.database.database import get_connection


class ServiceDisabledError(RuntimeError):
    """服务被用户关闭时抛出。调用方必须向上传递，禁止吞掉或回退。"""


class MissingCredentialError(RuntimeError):
    """服务已开启但缺少 API Key 时抛出，禁止静默切换到其他服务。"""


@dataclass
class UsageRecord:
    """一次 LLM 调用的用量。is_estimated=True 表示非接口返回的估算值。"""

    service: Service
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    task_id: str = ""
    success: bool = True
    error_type: str = ""
    is_estimated: bool = True
    cache_hit_tokens: int = 0
    occurred_at: datetime | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CostGuard:
    """付费闸门 + 成本账本。

    用法（服务调用方）：
        guard.require(Service.DEEPSEEK)      # 未开启 Key 或缺少 Key 时抛错，绝不回退
        ... 发起调用 ...
        guard.record_usage(UsageRecord(...)) # 记录实际/估算用量与费用
    """

    def __init__(self, config: Any) -> None:
        self.config = config

    # ---------- 闸门 ----------
    def require(self, service: Service) -> None:
        """调用付费服务前的强制检查。失败一律抛错，不允许静默回退到别的付费服务。"""
        if not self.config.is_enabled(service):
            self._log_check(service, False, f"service disabled: {service.value}")
            raise ServiceDisabledError(
                f"服务 [{service.value}] 已在成本控制中心关闭，调用被阻断。"
                f"如需使用，请由用户手动开启；系统不会自动切换到其他付费服务。"
            )
        if service in PAID_SERVICES and not self.config.has_api_key(service):
            self._log_check(service, False, f"missing credential: {service.value}")
            raise MissingCredentialError(
                f"服务 [{service.value}] 已开启但未检测到 API Key。"
                f"请设置环境变量 {self._credential_env(service)}；系统不会自动切换到其他付费服务。"
            )
        self._log_check(service, True, "allowed")

    def assert_no_fallback(self, failed_service: Service) -> None:
        """明确禁止回退的钩子。服务失败时调用，仅用于留痕，永不返回可用替代服务。"""
        self._log_check(failed_service, False, "call failed; fallback forbidden by policy")
        raise RuntimeError(
            f"服务 [{failed_service.value}] 调用失败。按成本控制策略，"
            f"禁止自动回退到其他付费服务。请检查配置或改为手动重试。"
        )

    def ensure_ai_media_disabled_by_default(self) -> list[str]:
        """审计当前配置：若付费 AI 图片/视频/在线 TTS 被开启，给出告警（不自动关闭）。"""
        warnings: list[str] = []
        for service in (Service.AI_IMAGE, Service.AI_VIDEO, Service.ONLINE_TTS):
            if self.config.is_enabled(service):
                warnings.append(
                    f"警告：付费服务 [{service.value}] 当前为开启状态。"
                    f"低成本默认应为关闭，请确认这是用户的主动选择。"
                )
        return warnings

    @staticmethod
    def _credential_env(service: Service) -> str:
        from PetAI.config.settings import API_KEY_ENV

        return API_KEY_ENV.get(service, "UNKNOWN")

    @staticmethod
    def _log_check(service: Service, allowed: bool, reason: str) -> None:
        """仅记录服务名与布尔结果，绝不记录 Key。"""
        from PetAI.utils.safe_log import get_logger

        get_logger("cost.guard").debug(
            "service_check service=%s allowed=%s reason=%s",
            service.value,
            allowed,
            reason,
        )
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO service_checks(service, allowed, reason) VALUES (?, ?, ?)",
                (service.value, int(allowed), reason),
            )
            conn.commit()
        except Exception:  # 审计日志失败不应阻断调用本身
            pass

    # ---------- 计费 ----------
    def _is_peak_hour(self, moment: datetime | None) -> bool:
        """按官方 UTC 时段判断是否高峰（周一至周五）。中国节假日无法自动识别，
        因此高峰期费用仍为估算上界，需以账单为准。"""
        if moment is None:
            moment = datetime.now(timezone.utc)
        if moment.weekday() >= 5:  # 周六/周日恒为非高峰
            return False
        raw_hours = self.config.get("deepseek", "peak_hours_utc", [1, 2, 3, 6, 7, 8, 9])
        try:
            peak_hours = {int(h) for h in (raw_hours or [])}
        except (TypeError, ValueError):
            peak_hours = {1, 2, 3, 6, 7, 8, 9}
        return moment.hour in peak_hours

    def calculate_cost(self, record: UsageRecord) -> float:
        """按可配置价格表计算费用。无价格条目时返回 0 并强制标记估算。

        支持官方结构：非高峰基准价、高峰倍率、缓存命中输入价。
        缓存命中 token 缺失时按普通输入价计（偏保守，标记估算）。
        """
        price = self.config.price_for(record.model)
        if not price:
            record.is_estimated = True
            return 0.0
        input_rate = float(price.get("input_per_million", 0) or 0)
        output_rate = float(price.get("output_per_million", 0) or 0)
        cache_rate = price.get("input_cache_hit_per_million")

        multiplier = 1.0
        if self._is_peak_hour(record.occurred_at):
            raw = self.config.get("deepseek", "peak_multiplier", 2.0)
            try:
                multiplier = float(raw)
            except (TypeError, ValueError):
                multiplier = 2.0

        hit = min(record.cache_hit_tokens, record.prompt_tokens)
        miss = record.prompt_tokens - hit
        input_cost = miss * input_rate
        if cache_rate is not None:
            input_cost += hit * float(cache_rate)
        else:
            input_cost += hit * input_rate
            if hit:
                record.is_estimated = True  # 无缓存价却出现命中，结果不精确

        cost = (
            input_cost / 1_000_000
            + record.completion_tokens / 1_000_000 * output_rate
        ) * multiplier
        return round(cost, 6)

    def record_usage(self, record: UsageRecord) -> float:
        """把一次调用写入成本账本，返回该次费用。"""
        if record.total_tokens <= 0:
            record.is_estimated = True
        cost = self.calculate_cost(record)
        conn = get_connection()
        conn.execute(
            """INSERT INTO cost_records
               (task_id, service, model, prompt_tokens, completion_tokens,
                total_tokens, is_estimated, cost_usd, currency, cache_hit_tokens,
                success, error_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.task_id,
                record.service.value,
                record.model,
                record.prompt_tokens,
                record.completion_tokens,
                record.total_tokens,
                int(record.is_estimated),
                cost,
                "USD",
                record.cache_hit_tokens,
                int(record.success),
                record.error_type,
            ),
        )
        conn.commit()
        from PetAI.utils.safe_log import get_logger

        get_logger("cost.ledger").info(
            "usage_recorded service=%s model=%s tokens=%d estimated=%s cost_usd=%.6f task=%s",
            record.service.value,
            record.model,
            record.total_tokens,
            record.is_estimated,
            cost,
            record.task_id,
        )
        return cost

    # ---------- 统计 ----------
    def task_cost(self, task_id: str) -> dict[str, Any]:
        """本次任务 API 消耗统计。"""
        conn = get_connection()
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(total_tokens), 0) AS tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost,
                      COALESCE(SUM(is_estimated), 0) AS estimated_calls
               FROM cost_records WHERE task_id = ?""",
            (task_id,),
        ).fetchone()
        return {
            "task_id": task_id,
            "calls": row["calls"],
            "tokens": row["tokens"],
            "cost_usd": round(row["cost"], 6),
            "currency": "USD",
            "estimated_calls": row["estimated_calls"],
        }

    def total_cost(self) -> dict[str, Any]:
        """累计费用统计（全部服务）。"""
        conn = get_connection()
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(total_tokens), 0) AS tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost,
                      COALESCE(SUM(is_estimated), 0) AS estimated_calls
               FROM cost_records"""
        ).fetchone()
        return {
            "calls": row["calls"],
            "tokens": row["tokens"],
            "cost_usd": round(row["cost"], 6),
            "currency": "USD",
            "estimated_calls": row["estimated_calls"],
        }

    def month_cost(self) -> dict[str, Any]:
        """本月估算费用统计。"""
        conn = get_connection()
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(total_tokens), 0) AS tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost,
                      COALESCE(SUM(is_estimated), 0) AS estimated_calls
               FROM cost_records
               WHERE created_at >= date('now', 'start of month')"""
        ).fetchone()
        return {
            "calls": row["calls"],
            "tokens": row["tokens"],
            "cost_usd": round(row["cost"], 6),
            "currency": "USD",
            "estimated_calls": row["estimated_calls"],
        }

    def status(self) -> dict[str, Any]:
        """成本控制中心仪表盘数据。"""
        from PetAI.config.settings import API_KEY_ENV

        services_status: dict[str, Any] = {}
        for service in Service:
            services_status[service.value] = {
                "enabled": self.config.is_enabled(service),
                "configured": (
                    self.config.has_api_key(service) if service in PAID_SERVICES else None
                ),
                "credential_env": API_KEY_ENV.get(service),
            }
        return {
            "services": services_status,
            "local_asset_first": bool(self.config.get("services", "local_asset_first", True)),
            "month": self.month_cost(),
            "total": self.total_cost(),
            "warnings": self.ensure_ai_media_disabled_by_default(),
            "active_model": self.config.get("deepseek", "model", ""),
            "note": (
                "费用单位 USD，基于官方可配置价格表；标记 estimated 或处于高峰时段的调用为估算值，"
                "中国节假日与缓存命中无法完全自动识别，最终以官方账单为准。"
            ),
        }
