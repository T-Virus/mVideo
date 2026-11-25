"""Windows 自带离线语音合成（SAPI5）配音服务。

背景：用户希望配音也用桌面豆包，但实测豆包朗读按钮只播放声音、不导出音频，
且无本地缓存、系统回环也录不到。因此改用 Windows 系统自带的 SAPI5 语音：
- 完全离线、零 API 费用、无需下载模型（Huihui 中文 / Zira 英文已随系统安装）；
- 生成 WAV 文件，直接进 PetAI 视频合成链路；
- 依赖 pywin32（可选），未安装时明确报错，不影响其他功能。

授权：使用操作系统自带语音，商用授权由用户自行确认（系统组件通常可随系统分发）。
"""
from __future__ import annotations

import pythoncom
import win32com.client
from pathlib import Path
from typing import Any

from PetAI.config.settings import PETAI_ROOT
from PetAI.utils.safe_log import get_logger

logger = get_logger("sapi_tts")

SAPI_AVAILABLE = True  # 导入成功即视为可用（异常在 failed_import 中捕获）


class SAPINotAvailable(RuntimeError):
    """Windows SAPI5 语音不可用（非 Windows 或 pywin32 未安装）。"""


class SAPITTSService:
    """Windows SAPI5 离线配音。"""

    DEFAULT_VOICES = {
        "zh": "Microsoft Huihui Desktop - Chinese (Simplified)",
        "en": "Microsoft Zira Desktop - English (United States)",
    }

    def __init__(self, config: Any, storage_root: str | Path | None = None) -> None:
        self.config = config
        self.audio_root = (
            Path(storage_root) if storage_root else (PETAI_ROOT / "storage" / "audio")
        )
        self.audio_root.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> dict[str, Any]:
        voices = self.list_voices()
        return {
            "available": bool(voices),
            "voices": voices,
            "note": "Windows 系统自带离线语音，零 API 费用；豆包朗读不导出音频，故用此方案替代。",
        }

    def list_voices(self) -> list[str]:
        pythoncom.CoInitialize()
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            return [v.GetDescription() for v in speaker.GetVoices()]
        except Exception as exc:  # noqa: BLE001
            logger.warning("SAPI 音色枚举失败: %s", exc)
            return []

    def _voice_for(self, language: str) -> str:
        configured = str(
            self.config.get(
                "tts",
                "sapi_voice_zh" if language == "zh" else "sapi_voice_en",
                "",
            )
            or ""
        ).strip()
        return configured or self.DEFAULT_VOICES.get(language, self.DEFAULT_VOICES["zh"])

    def synthesize(
        self,
        text: str,
        language: str = "zh",
        output_file: str | Path | None = None,
        rate: float = 1.0,
        volume: float = 1.0,
    ) -> dict[str, Any]:
        """文字转 WAV 配音。rate/volume 与 TTSService 保持一致（rate>1 更快）。"""
        if not text or not text.strip():
            raise ValueError("配音文本为空")
        output = Path(output_file) if output_file else (
            self.audio_root / f"sapi-{language}-{len(text)}.wav"
        )
        output.parent.mkdir(parents=True, exist_ok=True)

        # Python 进程内 COM 只需初始化一次；重复 CoUninitialize 在部分
        # pywin32 版本上会导致解释器退出时段错误，因此这里不配对释放。
        pythoncom.CoInitialize()
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            voices = speaker.GetVoices()
            wanted = self._voice_for(language)
            for voice in voices:
                if voice.GetDescription() == wanted:
                    speaker.Voice = voice
                    break
            else:
                # 找不到指定音色时按语言挑选第一个匹配的
                keyword = "Chinese" if language == "zh" else "English"
                for voice in voices:
                    if keyword.lower() in voice.GetDescription().lower():
                        speaker.Voice = voice
                        break

            speaker.Rate = max(-10, min(10, int(round((rate - 1.0) * 10))))
            speaker.Volume = max(0, min(100, int(volume * 100)))

            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(str(output), 3)  # SSFMCreateForWrite = 3
            speaker.AudioOutputStream = stream
            speaker.Speak(text)
            stream.Close()
        except Exception as exc:  # noqa: BLE001
            raise SAPINotAvailable(f"Windows SAPI5 配音失败：{exc}") from exc

        if not output.exists() or output.stat().st_size == 0:
            raise SAPINotAvailable("SAPI 未生成有效音频文件")
        used = speaker.Voice.GetDescription() if "speaker" in locals() else wanted
        logger.info("SAPI 配音完成 lang=%s voice=%s file=%s", language, used, output.name)
        return {
            "audio_path": str(output),
            "path": str(output),
            "source": "sapi-local",
            "engine": "sapi5-windows",
            "voice": used,
            "language": language,
        }
