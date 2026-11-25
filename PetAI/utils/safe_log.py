"""安全日志：任何日志输出前都经过 API Key 脱敏过滤。

保证任务书要求：API Key 不会出现在日志中。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from PetAI.config.settings import API_KEY_ENV

_CONFIGURED = False
_LOG_DIR = None

# 需要脱敏的环境变量名（Key 本身）
_SECRET_ENV_NAMES = set(API_KEY_ENV.values()) | {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}

_PATTERNS: list[re.Pattern[str]] = []


class _RedactFilter(logging.Filter):
    """在记录写出前，把已知 Key 与疑似密钥串替换为 ***REDACTED***。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        safe = redact(message)
        if safe != message:
            record.msg = safe
            record.args = ()
        return True


def refresh_secret_patterns() -> None:
    """从环境变量/.env 刷新待脱敏的 Key 字面量。每次配置变更后调用。"""
    _PATTERNS.clear()
    for name in _SECRET_ENV_NAMES:
        value = os.environ.get(name, "")
        if value and len(value) >= 4:
            _PATTERNS.append(re.compile(re.escape(value)))
    # 通用密钥形态：sk- 开头，或 deepseek 常见前缀
    _PATTERNS.append(re.compile(r"sk-[A-Za-z0-9_\-]{8,}"))


def redact(text: str) -> str:
    """把文本中的密钥替换为 ***REDACTED***。"""
    if not text:
        return text
    if not _PATTERNS:
        refresh_secret_patterns()
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub("***REDACTED***", result)
    return result


def configure_logging(log_dir: Any = None) -> None:
    """初始化 PetAI 专用 logger（写文件 + 脱敏过滤器）。"""
    global _CONFIGURED, _LOG_DIR
    if _CONFIGURED:
        return
    _LOG_DIR = log_dir
    refresh_secret_patterns()
    logger = logging.getLogger("PetAI")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(_RedactFilter())
    logger.addHandler(stream)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """返回带脱敏保护的 logger。"""
    configure_logging()
    child = logging.getLogger(f"PetAI.{name}")
    if not any(isinstance(f, _RedactFilter) for f in child.filters):
        child.addFilter(_RedactFilter())
    return child
