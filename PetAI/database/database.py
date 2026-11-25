"""PetAI 轻量 SQLite 持久化层。

只使用 Python 标准库 sqlite3，不引入新依赖，不触碰 MoneyPrinterTurbo 原有存储。
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from PetAI.config.settings import PETAI_ROOT

DB_PATH = PETAI_ROOT / "storage" / "petai.db"

# 建表语句。所有表使用 IF NOT EXISTS，重复初始化安全。
SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT    NOT NULL DEFAULT '',
    service         TEXT    NOT NULL,
    model           TEXT    NOT NULL DEFAULT '',
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    is_estimated    INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL DEFAULT 0.0,
    currency        TEXT    NOT NULL DEFAULT 'USD',
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    success         INTEGER NOT NULL DEFAULT 1,
    error_type      TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cost_records_task ON cost_records(task_id);
CREATE INDEX IF NOT EXISTS idx_cost_records_created ON cost_records(created_at);

CREATE TABLE IF NOT EXISTS service_checks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service      TEXT NOT NULL,
    allowed      INTEGER NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 阶段 3：宠物内容系统
CREATE TABLE IF NOT EXISTS topics (
    topic_id       TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    animal_type    TEXT NOT NULL DEFAULT 'both',     -- cat / dog / both
    category       TEXT NOT NULL DEFAULT 'entertainment',
    content_type   TEXT NOT NULL DEFAULT 'entertainment', -- science / entertainment / product
    description    TEXT NOT NULL DEFAULT '',
    target_audience TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT 'zh',
    priority       INTEGER NOT NULL DEFAULT 3,        -- 1 最高
    status         TEXT NOT NULL DEFAULT 'idea',      -- idea/selected/scripted/published/archived
    favorite       INTEGER NOT NULL DEFAULT 0,
    source         TEXT NOT NULL DEFAULT 'manual',    -- manual / ai
    asset_ids      TEXT NOT NULL DEFAULT '[]',        -- JSON 数组：关联素材
    task_id        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_topics_filter ON topics(animal_type, content_type, status);

CREATE TABLE IF NOT EXISTS knowledge (
    knowledge_id   TEXT PRIMARY KEY,
    topic          TEXT NOT NULL,
    animal_type    TEXT NOT NULL DEFAULT 'both',
    category       TEXT NOT NULL DEFAULT '',
    content        TEXT NOT NULL DEFAULT '',
    "references"   TEXT NOT NULL DEFAULT '[]',        -- JSON 数组：用户录入的可靠来源，AI 不得编造（引号避免保留字冲突）
    source_links   TEXT NOT NULL DEFAULT '[]',
    published_date TEXT NOT NULL DEFAULT '',
    verified_date  TEXT NOT NULL DEFAULT '',
    risk_level     TEXT NOT NULL DEFAULT 'A',         -- A/B/C 自动分级，可人工上调
    review_status  TEXT NOT NULL DEFAULT 'pending',   -- pending/approved/rejected
    review_note    TEXT NOT NULL DEFAULT '',
    reviewed_by    TEXT NOT NULL DEFAULT '',
    reviewed_at    TEXT NOT NULL DEFAULT '',
    author_identity TEXT NOT NULL DEFAULT 'learner',  -- learner（兽医学习者），禁止自动标为执业兽医
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_knowledge_review ON knowledge(review_status, risk_level);

CREATE TABLE IF NOT EXISTS scripts (
    script_id      TEXT PRIMARY KEY,
    topic_id       TEXT NOT NULL DEFAULT '',
    task_id        TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT 'zh',        -- zh / en
    script_type    TEXT NOT NULL DEFAULT 'science',    -- science / entertainment
    content        TEXT NOT NULL DEFAULT '',
    storyboard     TEXT NOT NULL DEFAULT '[]',         -- JSON：分镜
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    hashtags       TEXT NOT NULL DEFAULT '[]',
    asset_keywords TEXT NOT NULL DEFAULT '[]',
    knowledge_ids  TEXT NOT NULL DEFAULT '[]',
    review_status  TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scripts_topic ON scripts(topic_id, language);

CREATE TABLE IF NOT EXISTS publish_packs (
    pack_id        TEXT PRIMARY KEY,
    topic_id       TEXT NOT NULL DEFAULT '',
    task_id        TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT 'zh',
    video_path     TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    hashtags       TEXT NOT NULL DEFAULT '[]',
    thumbnail_text TEXT NOT NULL DEFAULT '',
    file_path      TEXT NOT NULL DEFAULT '',           -- 发布包文件路径
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_packs_topic ON publish_packs(topic_id, language);

-- 阶段 4：视频生产记录（中英双版本独立成片）
CREATE TABLE IF NOT EXISTS productions (
    production_id  TEXT PRIMARY KEY,
    topic_id       TEXT NOT NULL DEFAULT '',
    task_id        TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT 'zh',        -- zh / en
    status         TEXT NOT NULL DEFAULT 'pending',   -- pending/running/done/failed
    script_id      TEXT NOT NULL DEFAULT '',
    audio_path     TEXT NOT NULL DEFAULT '',
    subtitle_path  TEXT NOT NULL DEFAULT '',
    material_paths TEXT NOT NULL DEFAULT '[]',        -- JSON 数组
    output_path    TEXT NOT NULL DEFAULT '',
    tts_source     TEXT NOT NULL DEFAULT '',          -- piper-local / uploaded
    error          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_productions_topic ON productions(topic_id, language);
CREATE INDEX IF NOT EXISTS idx_productions_status ON productions(status);
"""

_local = threading.local()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """返回线程局部连接（SQLite 连接不可跨线程共享）。"""
    path = str(db_path or DB_PATH)
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
        _local.path = path
    return conn


def initialize_database(db_path: Path | str | None = None) -> Path:
    """初始化数据库与表结构，返回数据库文件路径。"""
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.executescript(SCHEMA)
        # 幂等迁移：兼容阶段 2 早期用 cost_cny 列的库
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cost_records)").fetchall()}
        if "cost_cny" in cols and "cost_usd" not in cols:
            conn.execute("ALTER TABLE cost_records RENAME COLUMN cost_cny TO cost_usd")
        for col, ddl in [
            ("currency", "TEXT NOT NULL DEFAULT 'USD'"),
            ("cache_hit_tokens", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in {r[1] for r in conn.execute("PRAGMA table_info(cost_records)").fetchall()}:
                conn.execute(f"ALTER TABLE cost_records ADD COLUMN {col} {ddl}")
        conn.commit()
    finally:
        conn.close()
    return path
