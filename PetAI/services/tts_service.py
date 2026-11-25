"""本地配音中心（任务书模块 6）。

硬性规则：
1. 只用本地 Piper，或用户自己上传的配音文件。
2. Piper 不可用时必须明确报错并提示上传，绝不能切换到在线/付费 TTS。
3. 本服务从不调用 MPT 的 app.services.voice.tts()——那条链路会走
   edge-tts / Azure / ElevenLabs 等在线服务，违反低成本原则。
4. 每次合成前过 LOCAL_TTS 闸门；失败时 assert_no_fallback() 留痕。
5. 本地 TTS 引擎与模型许可证单独登记（见 config.settings）。
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from PetAI.config.settings import PIPER_ENGINE_LICENSE, PIPER_MODEL_LICENSES, PETAI_ROOT, Service
from PetAI.services.cost_service import CostGuard
from PetAI.utils.safe_log import get_logger

logger = get_logger("tts")

LANG_ZH = "zh"
LANG_EN = "en"


class LocalTTSNotAvailable(RuntimeError):
    """本地 TTS 不可用（Piper 未安装/无模型），需用户上传配音。"""


class TTSService:
    def __init__(
        self,
        config: Any,
        guard: CostGuard,
        storage_root: str | Path | None = None,
    ) -> None:
        self.config = config
        self.guard = guard
        self.sapi: Any = None  # 延迟注入：Windows SAPI5 离线配音（Piper 的免费替代）
        self.audio_root = (
            Path(storage_root) if storage_root else (PETAI_ROOT / "storage" / "audio")
        )
        self.audio_root.mkdir(parents=True, exist_ok=True)

    # ---------- 能力与许可证 ----------
    def capabilities(self) -> dict[str, Any]:
        """给成本控制中心/界面展示：Piper 是否可用、许可证与语言支持。"""
        return {
            "local_tts_enabled": self.config.is_enabled(Service.LOCAL_TTS),
            "online_tts_enabled": self.config.is_enabled(Service.ONLINE_TTS),
            "piper_available": self.piper_available(),
            "upload_fallback": bool(self.config.get("tts", "allow_upload_fallback", True)),
            "engine_configured": str(self.config.get("tts", "engine", "piper") or "piper"),
            "sapi_available": bool(self.sapi and self.sapi.capabilities().get("available")),
            "engine_license": PIPER_ENGINE_LICENSE,
            "models": PIPER_MODEL_LICENSES,
            "note": (
                "Piper 为本地引擎；声音模型许可证以实际下载模型页为准。"
                "本服务绝不自动切换到在线/付费 TTS。"
            ),
        }

    def piper_available(self) -> bool:
        """检测 Piper 可执行文件是否可用（不假设已安装）。"""
        if not self.config.is_enabled(Service.LOCAL_TTS):
            return False
        configured = str(self.config.get("tts", "piper_executable", "") or "").strip()
        exe = configured or shutil.which("piper")
        return bool(exe) and Path(exe).is_file() if configured else bool(exe)

    def _piper_executable(self) -> str:
        configured = str(self.config.get("tts", "piper_executable", "") or "").strip()
        exe = configured or shutil.which("piper")
        if not exe:
            raise LocalTTSNotAvailable(
                "未检测到本地 Piper 引擎。请安装 Piper 并在 petai.toml 配置 "
                "[tts] piper_executable，或改用「上传自己的配音文件」。"
                "系统不会自动切换到在线/付费 TTS。"
            )
        return exe

    def _piper_model(self, language: str) -> str:
        key = "piper_model_zh" if language == LANG_ZH else "piper_model_en"
        model = str(self.config.get("tts", key, "") or "").strip()
        if not model or not Path(model).is_file():
            raise LocalTTSNotAvailable(
                f"未配置或找不到 {language} 的 Piper 模型文件（[tts].{key}）。"
                "请自行下载合规许可的 Piper 声音模型，或上传自己的配音文件。"
            )
        return model

    # ---------- 上传配音兜底 ----------
    def upload_voice(
        self,
        src_path: str | Path,
        language: str = LANG_ZH,
        license_info: str = "用户自有/已授权",
    ) -> dict[str, Any]:
        """登记用户上传的配音文件：复制入库、不覆盖、不改动原件。"""
        src = Path(src_path)
        if not src.is_file():
            raise FileNotFoundError(f"配音文件不存在: {src}")
        if not self.config.get("tts", "allow_upload_fallback", True):
            raise LocalTTSNotAvailable("上传配音兜底已在配置中关闭。")
        target = self.audio_root / f"voice-{language}-{uuid.uuid4().hex[:8]}{src.suffix.lower()}"
        shutil.copy2(str(src), str(target))
        info = {
            "path": str(target),
            "original_path": str(src),
            "language": language,
            "license": license_info,
            "source": "user-upload",
        }
        logger.info("已登记用户配音 file=%s lang=%s（原件未改动）", target.name, language)
        return info

    # ---------- 合成 ----------
    def synthesize(
        self,
        text: str,
        language: str = LANG_ZH,
        output_file: str | Path | None = None,
        voice_file: str | Path | None = None,
        task_id: str = "",
    ) -> dict[str, Any]:
        """生成配音。

        voice_file 非空时直接使用用户配音（上传兜底），不调用任何 TTS 引擎。
        否则尝试本地 Piper；不可用则抛 LocalTTSNotAvailable，绝不回退在线服务。
        """
        self.guard.require(Service.LOCAL_TTS)
        if not text.strip() and not voice_file:
            raise ValueError("配音文本为空且未提供配音文件。")

        # 上传兜底：用户配音优先，绝不动任何在线服务。
        if voice_file:
            vf = Path(voice_file)
            if not vf.is_file():
                raise FileNotFoundError(f"用户配音文件不存在: {vf}")
            logger.info("使用用户上传配音，跳过所有 TTS 引擎 task=%s", task_id)
            return {
                "audio_path": str(vf),
                "source": "uploaded",
                "language": language,
                "engine": "none",
                "path": str(vf),
            }

        # 引擎选择：piper（需模型）/ sapi（Windows 自带，零成本）。
        engine = str(self.config.get("tts", "engine", "piper") or "piper").strip().lower()
        volume = float(self.config.get("tts", "volume", 1.0))
        rate = float(self.config.get("tts", "rate", 1.0))
        if engine == "sapi" or (not self.piper_available() and self.sapi is not None):
            if self.sapi is None:
                raise LocalTTSNotAvailable(
                    "已选择 SAPI 配音但未注入服务；请检查 bootstrap 装配。"
                )
            result = self.sapi.synthesize(
                text, language=language, output_file=output_file,
                rate=rate, volume=volume,
            )
            logger.info("SAPI 本地配音完成 lang=%s file=%s task=%s", language, result["path"], task_id)
            return result

        # 本地 Piper。
        exe = self._piper_executable()
        model = self._piper_model(language)
        output = Path(output_file) if output_file else (
            self.audio_root / f"tts-{language}-{uuid.uuid4().hex[:8]}.wav"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        volume = float(self.config.get("tts", "volume", 1.0))
        rate = float(self.config.get("tts", "rate", 1.0))
        # Piper 用 --length-scale 控制语速（>1 更慢），音量由后处理处理。
        length_scale = 1.0 / rate if rate > 0 else 1.0
        cmd = [
            exe,
            "--model", model,
            "--output_file", str(output),
            "--length-scale", f"{length_scale:.3f}",
        ]
        try:
            result = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
                check=False,
            )
        except Exception as exc:
            self.guard.assert_no_fallback(Service.LOCAL_TTS)  # 明确拒绝回退（会抛错）
            raise exc  # pragma: no cover - assert_no_fallback 已抛错
        if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            detail = result.stderr.decode("utf-8", errors="replace")[-500:]
            self.guard.assert_no_fallback(Service.LOCAL_TTS)
            raise RuntimeError(f"本地 Piper 合成失败: {detail}")
        if volume != 1.0:
            self._apply_volume(output, volume)
        logger.info("本地 Piper 合成完成 lang=%s file=%s task=%s", language, output.name, task_id)
        return {
            "audio_path": str(output),
            "source": "piper-local",
            "language": language,
            "engine": "piper",
            "model": Path(model).name,
            "path": str(output),
        }

    @staticmethod
    def _apply_volume(wav_path: Path, volume: float) -> None:
        """用 ffmpeg 调整音量（就地处理，临时文件原子替换）。"""
        from app.utils import utils as mpt_utils  # 复用 MPT 的 ffmpeg 解析

        ffmpeg = mpt_utils.get_ffmpeg_binary()
        tmp = wav_path.with_suffix(".vol.wav")
        cmd = [
            ffmpeg, "-y", "-i", str(wav_path),
            "-af", f"volume={volume:.3f}", str(tmp),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0 and tmp.exists():
            tmp.replace(wav_path)
        else:
            logger.warning("音量调整失败，保留原始音频: %s", wav_path.name)
