"""PetAI 依赖装配入口。所有服务共享同一份配置与同一个 CostGuard。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PetAI.config.settings import PetConfig
from PetAI.database.database import initialize_database
from PetAI.services.cost_service import CostGuard
from PetAI.services.deepseek_service import DeepSeekService
from PetAI.services.local_asset_service import LocalAssetService
from PetAI.services.topic_service import TopicService
from PetAI.services.knowledge_service import KnowledgeService
from PetAI.services.script_service import ScriptService
from PetAI.services.publish_service import PublishService
from PetAI.services.tts_service import TTSService
from PetAI.services.subtitle_service import SubtitleService
from PetAI.services.video_service import VideoService
from PetAI.utils.safe_log import configure_logging, refresh_secret_patterns


@dataclass
class PetApp:
    config: PetConfig
    guard: CostGuard
    deepseek: DeepSeekService
    assets: LocalAssetService
    topics: TopicService
    knowledge: KnowledgeService
    scripts: ScriptService
    publish: PublishService
    tts: TTSService
    subtitles: SubtitleService
    video: VideoService

    def status(self) -> dict[str, Any]:
        data = self.guard.status()
        data["tts_capabilities"] = self.tts.capabilities()
        return data


def create_app(
    config_path: str | None = None,
    env_path: str | None = None,
    init_db: bool = True,
) -> PetApp:
    config = PetConfig(config_path=config_path, env_path=env_path)
    configure_logging()
    refresh_secret_patterns()
    if init_db:
        initialize_database()
    guard = CostGuard(config)
    deepseek = DeepSeekService(config, guard)
    knowledge = KnowledgeService(config, guard, deepseek)
    assets = LocalAssetService(config, guard)
    tts = TTSService(config, guard)
    subtitles = SubtitleService(config, guard)
    return PetApp(
        config=config,
        guard=guard,
        deepseek=deepseek,
        assets=assets,
        topics=TopicService(config, guard, deepseek),
        knowledge=knowledge,
        scripts=ScriptService(config, guard, deepseek, knowledge),
        publish=PublishService(config, guard),
        tts=tts,
        subtitles=subtitles,
        video=VideoService(config, guard, assets, tts, subtitles),
    )
