"""Pet AI Content Factory 配置加载层。

设计原则（对应任务书模块 1 / 模块 2）：
1. 付费服务默认全部关闭，DeepSeek 默认开启（唯一默认付费 AI 服务）。
2. API Key 只从环境变量或 .env 读取，绝不写入配置文件、前端代码或日志。
3. 模型价格表从 petai.toml 读取，不硬编码在代码中。
4. 本模块不修改 MoneyPrinterTurbo 原有 config.toml，保证原项目配置兼容性。
"""
from __future__ import annotations

import copy
import os
from enum import Enum
from pathlib import Path
from typing import Any

import toml


class Service(str, Enum):
    """受成本控制中心管理的服务标识。"""

    DEEPSEEK = "deepseek"
    AI_IMAGE = "ai_image"
    AI_VIDEO = "ai_video"
    ONLINE_TTS = "online_tts"
    LOCAL_TTS = "local_tts"
    NETWORK_ASSET = "network_asset"


# 需要付费或可能产生费用的服务（本地 TTS 不在其列）。
PAID_SERVICES = frozenset(
    {
        Service.DEEPSEEK,
        Service.AI_IMAGE,
        Service.AI_VIDEO,
        Service.ONLINE_TTS,
        Service.NETWORK_ASSET,
    }
)

# 服务对应的 API Key 环境变量名（唯一读取来源）。
API_KEY_ENV = {
    Service.DEEPSEEK: "DEEPSEEK_API_KEY",
    Service.AI_IMAGE: "PETAI_AI_IMAGE_API_KEY",
    Service.AI_VIDEO: "PETAI_AI_VIDEO_API_KEY",
    Service.ONLINE_TTS: "PETAI_ONLINE_TTS_API_KEY",
}

# 默认值：付费服务关闭，DeepSeek 与本地 TTS 开启，本地素材优先。
DEFAULT_CONFIG: dict[str, Any] = {
    "services": {
        "deepseek": True,
        "ai_image": False,
        "ai_video": False,
        "online_tts": False,
        "local_tts": True,
        "network_asset": False,
        "local_asset_first": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        # 以官方 /models 返回为准：当前为 deepseek-flash / deepseek-v4-pro。
        # deepseek-chat / deepseek-reasoner 已退役（仅兼容路由），不要再用。
        "model": "deepseek-flash",
        "timeout": 60,
        "max_retries": 2,
        # 默认关闭 thinking：更便宜更快；需要深度推理时可改为 true
        "thinking_enabled": False,
        # 高峰时段（UTC 小时集合），官方高峰价为非高峰的 2 倍。
        # 官方定义：周一至周五 01:00-04:00 与 06:00-10:00 UTC（中国节假日除外）。
        "peak_hours_utc": [1, 2, 3, 6, 7, 8, 9],
        "peak_multiplier": 2.0,
    },
    # 本地 TTS（Piper）：默认启用本地；Piper 未安装时允许上传配音兜底。
    # 绝不自动切换到在线/付费 TTS。模型许可证与语言支持单独记录。
    "tts": {
        "engine": "piper",            # piper / uploaded（只影响默认尝试顺序）
        "piper_executable": "",       # 留空则从 PATH 查找 piper
        "piper_model_zh": "",         # 中文模型 .onnx 路径（用户自行下载）
        "piper_model_en": "",         # 英文模型 .onnx 路径
        "volume": 1.0,
        "rate": 1.0,
        "allow_upload_fallback": True,
    },
    # 视频生产：复用 MPT 合成引擎，固定 9:16 竖屏。
    "video": {
        "aspect": "9:16",
        "width": 1080,
        "height": 1920,
        "clip_duration": 5,
        "font_name": "STHeitiMedium.ttc",
        "subtitle_enabled": True,
        "bgm_type": "random",
        "logo_path": "",              # 品牌 Logo（可选，需 MPT 支持图层）
    },
    # 价格表：官方美元价 / 1M tokens（非高峰、cache miss 为基准）。
    # 不在代码中写死；无条目时费用记为 0 并标记「估算」。请定期核对官方定价页。
    "pricing": {},
}

# 本地 TTS 模型许可证与语言支持登记（任务书模块 6 要求单独记录）。
# 说明：Piper 引擎为 MIT 许可；具体声音模型多为 CC-BY 4.0，须按所下载模型核对。
PIPER_ENGINE_LICENSE = "MIT (https://github.com/rhasspy/piper)"
PIPER_MODEL_LICENSES: dict[str, dict[str, str]] = {
    "zh_CN-huayan-medium": {
        "language": "zh-CN",
        "license": "CC-BY 4.0 (需按模型页核对)",
        "source": "https://huggingface.co/rhasspy/piper-voices",
    },
    "en_US-lessac-medium": {
        "language": "en-US",
        "license": "CC-BY 4.0 (需按模型页核对)",
        "source": "https://huggingface.co/rhasspy/piper-voices",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置，override 优先，保留 base 中未覆盖的默认值。

    必须深拷贝默认值，否则多个配置实例会共享同一份嵌套 dict，
    一个实例 set_service() 会污染全局默认与其他实例。
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_env_file(path: Path) -> dict[str, str]:
    """解析 .env（KEY=VALUE），忽略注释与空行。环境变量优先级更高。"""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def mask_secret(value: str | None, keep: int = 4) -> str:
    """返回可安全写入日志的脱敏串。"""
    if not value:
        return "<unset>"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep)}"


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PETAI_ROOT = PROJECT_ROOT / "PetAI"


class PetConfig:
    """PetAI 配置对象。配置文件缺失时回退到 DEFAULT_CONFIG。"""

    def __init__(
        self,
        config_path: Path | str | None = None,
        env_path: Path | str | None = None,
    ) -> None:
        self.root = PROJECT_ROOT
        self.config_path = Path(config_path) if config_path else PETAI_ROOT / "config" / "petai.toml"
        self.env_path = Path(env_path) if env_path else self.root / ".env"
        self._toml_data: dict[str, Any] = {}
        self._env_data: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self._toml_data = {}
        if self.config_path.exists():
            loaded = toml.load(str(self.config_path))
            if isinstance(loaded, dict):
                self._toml_data = loaded
        self._env_data = _parse_env_file(self.env_path)
        # 合并默认值与文件内容
        self.data = _deep_merge(DEFAULT_CONFIG, self._toml_data)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

    def is_enabled(self, service: Service) -> bool:
        return bool(self.data.get("services", {}).get(service.value, False))

    def set_service(self, service: Service, enabled: bool) -> None:
        self.data.setdefault("services", {})[service.value] = bool(enabled)

    def api_key(self, service: Service) -> str | None:
        """从环境变量或 .env 读取 Key；环境变量优先。绝不落盘到配置或日志。"""
        env_name = API_KEY_ENV.get(service)
        if not env_name:
            return None
        value = os.environ.get(env_name) or self._env_data.get(env_name)
        if not value:
            return None
        # 忽略模板占位值，避免把"未配置"误判为"已连接"
        lowered = value.lower()
        if "your_" in lowered or lowered in {"changeme", "placeholder", "none", "todo"}:
            return None
        return value

    def has_api_key(self, service: Service) -> bool:
        return bool(self.api_key(service))

    def price_for(self, model: str) -> dict[str, float] | None:
        """按精确名或最长前缀从可配置价格表匹配模型单价。"""
        pricing = self.data.get("pricing", {}) or {}
        if not isinstance(pricing, dict):
            return None
        if model in pricing:
            entry = pricing[model]
            return entry if isinstance(entry, dict) else None
        best_key = None
        for key in pricing:
            if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
                best_key = key
        if best_key:
            entry = pricing[best_key]
            return entry if isinstance(entry, dict) else None
        return None

    def save(self) -> None:
        """把当前配置写回 petai.toml（仅配置，不含任何 Key）。"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(toml.dumps(self.data), encoding="utf-8")

    def public_dict(self) -> dict[str, Any]:
        """给界面/API 使用的脱敏视图，任何 Key 都不会出现。"""
        return {
            "services": dict(self.data.get("services", {})),
            "deepseek": {
                "base_url": self.get("deepseek", "base_url"),
                "model": self.get("deepseek", "model"),
                "configured": self.has_api_key(Service.DEEPSEEK),
            },
            "pricing": dict(self.data.get("pricing", {})),
            "tts": {
                "engine": self.get("tts", "engine", "piper"),
                "piper_configured": bool(self.get("tts", "piper_executable", "")),
                "volume": self.get("tts", "volume", 1.0),
                "rate": self.get("tts", "rate", 1.0),
                "allow_upload_fallback": bool(
                    self.get("tts", "allow_upload_fallback", True)
                ),
                "engine_license": PIPER_ENGINE_LICENSE,
                "model_licenses": PIPER_MODEL_LICENSES,
            },
            "video": dict(self.data.get("video", {})),
        }
