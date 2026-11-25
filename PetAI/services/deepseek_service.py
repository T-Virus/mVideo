"""DeepSeek 文案服务（唯一默认付费 AI 服务）。

对应任务书模块 2：
- API Key 只从环境变量 / .env 读取，禁止写入前端代码与日志；
- 连接测试、模型可配置、超时与重试、结构化 JSON 输出；
- 记录实际 Token（接口返回则用实际值，否则标记估算）；
- 失败时明确报错，绝不自动切换到其他付费 provider。
"""
from __future__ import annotations

import json
import time
from typing import Any

from PetAI.config.settings import Service
from PetAI.services.cost_service import (
    CostGuard,
    MissingCredentialError,
    ServiceDisabledError,
    UsageRecord,
)
from PetAI.utils.safe_log import get_logger, redact

logger = get_logger("deepseek")


class DeepSeekError(RuntimeError):
    """DeepSeek 调用失败。调用方应把错误展示给用户，而不是切换服务。"""


class DeepSeekService:
    def __init__(self, config: Any, guard: CostGuard) -> None:
        self.config = config
        self.guard = guard

    # ---------- 连接参数 ----------
    @property
    def model(self) -> str:
        return str(self.config.get("deepseek", "model", "deepseek-chat") or "deepseek-chat")

    @property
    def base_url(self) -> str:
        return str(self.config.get("deepseek", "base_url", "https://api.deepseek.com"))

    @property
    def timeout(self) -> float:
        return float(self.config.get("deepseek", "timeout", 60))

    @property
    def max_retries(self) -> int:
        return max(1, int(self.config.get("deepseek", "max_retries", 2)))

    @property
    def thinking_enabled(self) -> bool:
        """默认关闭 thinking：更便宜、更快、正文直接可用。需要深度推理时可配置开启。"""
        return bool(self.config.get("deepseek", "thinking_enabled", False))

    def _thinking_kwargs(self) -> dict[str, Any]:
        """官方 V4 系列用 extra_body.thinking 切换思维模式。"""
        return {"extra_body": {"thinking": {"type": "enabled" if self.thinking_enabled else "disabled"}}}

    def _client(self) -> Any:
        """惰性构造 OpenAI 兼容客户端（复用原项目已安装的 openai SDK）。"""
        self.guard.require(Service.DEEPSEEK)  # 闸门：关闭或缺 Key 时抛错
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 环境依赖
            raise DeepSeekError("未安装 openai SDK，无法调用 DeepSeek。请运行依赖安装脚本。") from exc
        api_key = self.config.api_key(Service.DEEPSEEK)
        # Key 只在此处传入 SDK，不打印、不写入任何配置或日志
        return OpenAI(api_key=api_key, base_url=self.base_url, timeout=self.timeout)

    # ---------- 连接测试 ----------
    def test_connection(self, task_id: str = "connection-test") -> dict[str, Any]:
        """连通性测试：发一条极短请求，记录 Token，返回可用模型与状态。"""
        record = UsageRecord(service=Service.DEEPSEEK, model=self.model, task_id=task_id)
        try:
            client = self._client()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "回复 OK 两个字"}],
                max_tokens=16,
                **self._thinking_kwargs(),
            )
            usage = getattr(resp, "usage", None)
            self._apply_usage(record, usage, estimated=False)
            content = resp.choices[0].message.content if resp.choices else ""
            self.guard.record_usage(record)
            return {
                "ok": True,
                "model": self.model,
                "message": str(content).strip()[:50],
                "tokens": record.total_tokens,
            }
        except (ServiceDisabledError, MissingCredentialError):
            # 策略拒绝不记账、不包装，直接透传
            raise
        except Exception as exc:
            record.success = False
            record.error_type = type(exc).__name__
            self.guard.record_usage(record)
            raise self._wrap_error(exc) from exc

    def list_models(self, task_id: str = "model-discovery") -> list[str]:
        """列出官方当前可用模型，用于核对 model 配置（不写死模型名）。"""
        client = self._client()
        try:
            models = client.models.list()
            return [m.id for m in models.data]
        except Exception as exc:  # 部分兼容端点不支持 /models
            raise self._wrap_error(exc) from exc

    # ---------- 文本生成 ----------
    def generate(
        self,
        prompt: str,
        task_id: str = "",
        json_mode: bool = False,
        temperature: float = 0.7,
    ) -> str:
        """生成文本。json_mode=True 时要求返回结构化 JSON 字符串。"""
        record = UsageRecord(service=Service.DEEPSEEK, model=self.model, task_id=task_id)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                client = self._client()
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                kwargs.update(self._thinking_kwargs())
                resp = client.chat.completions.create(**kwargs)
                usage = getattr(resp, "usage", None)
                self._apply_usage(record, usage, estimated=usage is None)
                self.guard.record_usage(record)
                content = resp.choices[0].message.content if resp.choices else ""
                return str(content or "")
            except (ServiceDisabledError, MissingCredentialError):
                # 闸门策略拒绝不是网络故障：不重试、不记账、不伪装成"调用失败"。
                # 直接向上透传，让用户明确知道服务被关闭或缺 Key。
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("DeepSeek 第 %d/%d 次调用失败: %s", attempt, self.max_retries, type(exc).__name__)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 8))
        # 失败：记录失败并明确禁止回退
        record.success = False
        record.error_type = type(last_error).__name__ if last_error else "Unknown"
        self.guard.record_usage(record)
        wrapped = self._wrap_error(last_error or RuntimeError("unknown error"))
        logger.error("DeepSeek 调用最终失败，禁止回退到其他付费服务: %s", wrapped)
        self.guard.assert_no_fallback(Service.DEEPSEEK)  # 永不返回，强制阻断
        raise wrapped  # pragma: no cover - assert_no_fallback 已抛错

    def generate_json(self, prompt: str, task_id: str = "") -> Any:
        """生成并解析 JSON；解析失败抛出 DeepSeekError，不猜测、不回退。"""
        text = self.generate(prompt, task_id=task_id, json_mode=True)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DeepSeekError("DeepSeek 返回的 JSON 无法解析，请重试或手动输入脚本。") from exc

    # ---------- 内部 ----------
    @staticmethod
    def _apply_usage(record: UsageRecord, usage: Any, estimated: bool) -> None:
        from datetime import datetime, timezone

        record.occurred_at = datetime.now(timezone.utc)
        if usage is not None:
            record.prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            record.completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            record.is_estimated = False
            # 缓存命中 token：不同 SDK 版本字段名可能不同
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                hit = getattr(details, "cached_tokens", 0) or 0
                record.cache_hit_tokens = int(hit)
        else:
            record.is_estimated = True

    @staticmethod
    def _wrap_error(exc: Exception) -> DeepSeekError:
        # 只暴露类型与通用信息，避免把可能含 Key 的原始错误串写入日志
        return DeepSeekError(
            f"DeepSeek 调用失败（{type(exc).__name__}）。"
            f"请检查网络、Key、模型名与额度；系统不会自动切换到其他付费服务。"
        )
