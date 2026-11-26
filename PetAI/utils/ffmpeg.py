"""FFmpeg 路径解析：优先 imageio-ffmpeg，避免依赖顶层 `app` 包名。

Streamlit 运行时会把脚本目录（PetAI）放进 sys.path 前面，`import app`
可能解析到 PetAI/app，导致 `from app.utils import utils` 报 No module named 'app.utils'。
imageio-ffmpeg 是独立包，不受这个冲突影响。
"""
from __future__ import annotations


def ffmpeg_binary() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - 回退到 MPT 的解析逻辑
        from app.utils import utils as mpt_utils

        return mpt_utils.get_ffmpeg_binary()
