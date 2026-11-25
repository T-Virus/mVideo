"""通用 SQLite 记录存取助手，供内容服务复用。"""
from __future__ import annotations

import json
from typing import Any

from PetAI.database.database import get_connection


def _q(col: str) -> str:
    """列名加双引号，避免 references 等 SQL 保留字冲突。"""
    return f'"{col}"'


def insert(table: str, record: dict[str, Any]) -> None:
    cols = ",".join(_q(c) for c in record.keys())
    placeholders = ",".join("?" for _ in record)
    conn = get_connection()
    conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
        tuple(record.values()),
    )
    conn.commit()


def get(table: str, key_col: str, key: str) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    return dict(row) if row else None


def list_rows(
    table: str,
    where: str = "",
    params: tuple = (),
    order_by: str = "created_at DESC",
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    sql += " LIMIT ?"
    conn = get_connection()
    rows = conn.execute(sql, params + (limit,)).fetchall()
    return [dict(r) for r in rows]


def update(table: str, key_col: str, key: str, changes: dict[str, Any]) -> int:
    if not changes:
        return 0
    assignments = ",".join(f"{_q(k)} = ?" for k in changes)
    conn = get_connection()
    cur = conn.execute(
        f"UPDATE {table} SET {assignments} WHERE {key_col} = ?",
        tuple(changes.values()) + (key,),
    )
    conn.commit()
    return cur.rowcount


def delete(table: str, key_col: str, key: str) -> int:
    conn = get_connection()
    cur = conn.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key,))
    conn.commit()
    return cur.rowcount


def load_json(value: str | None) -> list[Any]:
    """安全解析 JSON 数组字段。"""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def dump_json(value: list[Any]) -> str:
    return json.dumps(value, ensure_ascii=False)
