"""测试夹具：内存隔离数据库 + 临时配置，避免污染真实 PetAI 数据。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PetAI.config.settings import PetConfig  # noqa: E402
from PetAI.database import database as dbmod  # noqa: E402
from PetAI.services.cost_service import CostGuard  # noqa: E402


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """把全局 DB 路径指向临时库，并清理线程局部连接。"""
    path = tmp_path / "test.db"
    dbmod.initialize_database(path)
    monkeypatch.setattr(dbmod, "DB_PATH", path)
    if hasattr(dbmod._local, "conn"):
        del dbmod._local.conn
    if hasattr(dbmod._local, "path"):
        del dbmod._local.path
    return path


@pytest.fixture
def base_config(tmp_path):
    """默认配置：付费服务关闭，DeepSeek/本地TTS开启，Key 由环境变量注入。"""
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test-secret-key-1234567890\n", encoding="utf-8")
    cfg = PetConfig(env_path=env, config_path=tmp_path / "petai.toml")
    return cfg


@pytest.fixture
def guard(base_config, temp_db):
    return CostGuard(base_config)


class MockDeepSeek:
    """离线替身：不触网、不计费，返回可预测的 JSON/文本。"""

    def __init__(self):
        self.calls = []

    def generate(self, prompt, task_id="", json_mode=False, temperature=0.7):
        self.calls.append(("text", prompt, task_id))
        if "整理并润色" in prompt or "润色" in prompt:
            return "整理后的正文：猫会打呼噜是因为放松。"
        return "OK"

    def generate_json(self, prompt, task_id=""):
        self.calls.append(("json", prompt, task_id))
        if "选题" in prompt and "topics" in prompt:
            return {"topics": [
                {"title": "猫为什么爱钻纸箱", "description": "解密猫咪的纸箱情结",
                 "target_audience": "猫主人", "category": "cat_behavior"},
                {"title": "狗狗摇尾巴的真相", "description": "尾巴语言入门",
                 "target_audience": "狗主人", "category": "dog_behavior"},
            ]}
        if "英文" in prompt and "Rewrite" in prompt:
            return {"script": "Why cats love boxes", "title": "Cat Box Mystery",
                    "description": "A quick science take", "hashtags": ["#cats", "#science"]}
        # 默认脚本返回
        is_en = "English" in prompt or "English-speaking" in prompt
        return {
            "script": "Why cats love boxes" if is_en else "猫为什么爱钻纸箱：原来纸箱让猫咪更有安全感。",
            "storyboard": [
                {"scene": 1, "description": "猫咪钻进纸箱", "asset_keyword": "cat box"},
                {"scene": 2, "description": "科普字幕", "asset_keyword": "cat portrait"},
            ],
            "title": "Cat Box Mystery" if is_en else "猫为什么爱钻纸箱",
            "description": "A quick science take" if is_en else "一分钟了解猫咪的纸箱情结",
            "hashtags": ["#cats", "#science"] if is_en else ["#猫咪", "#科普"],
            "asset_keywords": ["cat box", "cat portrait"],
        }


@pytest.fixture
def mock_deepseek():
    return MockDeepSeek()


@pytest.fixture
def topic_svc(base_config, guard, mock_deepseek):
    from PetAI.services.topic_service import TopicService
    return TopicService(base_config, guard, mock_deepseek)


@pytest.fixture
def knowledge_svc(base_config, guard, mock_deepseek):
    from PetAI.services.knowledge_service import KnowledgeService
    return KnowledgeService(base_config, guard, mock_deepseek)


@pytest.fixture
def script_svc(base_config, guard, mock_deepseek, knowledge_svc):
    from PetAI.services.script_service import ScriptService
    return ScriptService(base_config, guard, mock_deepseek, knowledge_svc)


@pytest.fixture
def publish_svc(base_config, guard, tmp_path):
    from PetAI.services.publish_service import PublishService
    svc = PublishService(base_config, guard)
    svc.export_root = tmp_path / "exports"
    return svc


@pytest.fixture
def tts_svc(base_config, guard, tmp_path):
    from PetAI.services.tts_service import TTSService
    return TTSService(base_config, guard, storage_root=tmp_path / "audio")


@pytest.fixture
def subtitle_svc(base_config, guard, tmp_path):
    from PetAI.services.subtitle_service import SubtitleService
    return SubtitleService(base_config, guard, storage_root=tmp_path / "subtitles")


@pytest.fixture
def video_svc(base_config, guard, tmp_path, tts_svc, subtitle_svc):
    """视频服务：注入伪合成函数，只验证编排与参数，不触发真实编码。"""
    from PetAI.services.local_asset_service import LocalAssetService
    from PetAI.services.video_service import VideoService

    assets = LocalAssetService(base_config, guard, storage_root=tmp_path / "assets")
    calls: dict[str, Any] = {}

    def fake_combine(**kwargs):
        calls["combine"] = kwargs
        Path(kwargs["combined_video_path"]).write_bytes(b"combined")
        return kwargs["combined_video_path"]

    def fake_generate(**kwargs):
        calls["generate"] = kwargs
        Path(kwargs["output_file"]).write_bytes(b"mp4")
        return True

    svc = VideoService(
        base_config, guard, assets, tts_svc, subtitle_svc,
        storage_root=tmp_path / "exports",
        combine_videos=fake_combine,
        generate_video=fake_generate,
    )
    svc.calls = calls
    return svc
