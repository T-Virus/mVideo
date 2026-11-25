"""PetAI 依赖装配入口。所有服务共享同一份配置与同一个 CostGuard。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PetAI.config.settings import PetConfig
from PetAI.database.database import initialize_database
from PetAI.services.cost_service import CostGuard
from PetAI.services.deepseek_service import DeepSeekService
from PetAI.services.doubao_desktop_service import DoubaoDesktopService
from PetAI.services.doubao_script_service import DoubaoScriptService
from PetAI.services.hot_topic_service import HotTopicService
from PetAI.services.local_asset_service import LocalAssetService
from PetAI.services.pipeline_service import PipelineService
from PetAI.services.topic_service import TopicService
from PetAI.services.knowledge_service import KnowledgeService
from PetAI.services.script_service import ScriptService
from PetAI.services.publish_service import PublishService
from PetAI.services.tts_service import TTSService
from PetAI.services.sapi_tts_service import SAPITTSService
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
    doubao: DoubaoDesktopService
    doubao_scripts: DoubaoScriptService
    sapi: SAPITTSService
    hot: HotTopicService
    pipeline: PipelineService

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
    deepseek = DeepSeekService(config, guard)  # 默认关闭，仅供手动启用
    assets = LocalAssetService(config, guard)
    tts = TTSService(config, guard)
    sapi = SAPITTSService(config)
    tts.sapi = sapi  # Windows 自带离线语音，零成本
    subtitles = SubtitleService(config, guard)
    # 先装配豆包，再注入到所有文本生成服务（脚本 / 选题 / 知识整理）
    doubao = DoubaoDesktopService(config, assets)
    doubao_scripts = DoubaoScriptService(config, doubao)
    knowledge = KnowledgeService(config, guard, deepseek, doubao_scripts)
    topics = TopicService(config, guard, deepseek, doubao_scripts)
    scripts = ScriptService(config, guard, deepseek, knowledge, doubao_scripts)
    publish = PublishService(config, guard)
    video = VideoService(config, guard, assets, tts, subtitles)
    hot = HotTopicService(config, topics, doubao_scripts)
    return PetApp(
        config=config,
        guard=guard,
        deepseek=deepseek,
        assets=assets,
        topics=topics,
        knowledge=knowledge,
        scripts=scripts,
        publish=publish,
        tts=tts,
        subtitles=subtitles,
        video=video,
        doubao=doubao,
        doubao_scripts=doubao_scripts,
        sapi=sapi,
        hot=hot,
        pipeline=PipelineService(
            config, hot, scripts, knowledge, doubao, assets, video, publish
        ),
    )
