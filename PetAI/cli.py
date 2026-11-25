"""PetAI 命令行工具：初始化、状态查看、可选付费连接测试。

用法：
  python -m PetAI.cli init          # 初始化数据库与配置模板
  python -m PetAI.cli status        # 查看本地引擎与付费闸门状态（默认应全部关闭）
  python -m PetAI.cli test-deepseek # 仅在你手动启用 DeepSeek 后使用
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from PetAI.config.settings import PETAI_ROOT, PROJECT_ROOT
from PetAI.database.database import initialize_database


def cmd_init() -> int:
    db = initialize_database()
    tpl = PETAI_ROOT / "config" / "petai.example.toml"
    target = PETAI_ROOT / "config" / "petai.toml"
    if not target.exists() and tpl.exists():
        shutil.copy2(tpl, target)
        print(f"[init] 已创建配置: {target}")
    else:
        print(f"[init] 配置已存在或模板缺失，跳过: {target}")
    env_tpl = PROJECT_ROOT / ".env.example"
    env_target = PROJECT_ROOT / ".env"
    if not env_target.exists() and env_tpl.exists():
        shutil.copy2(env_tpl, env_target)
        print(f"[init] 已创建 .env 模板: {env_target}（默认方案无需填写 Key）")
    else:
        print(f"[init] .env 已存在或模板缺失，跳过")
    print(f"[init] 数据库就绪: {db}")
    return 0


def cmd_status() -> int:
    from PetAI.app.bootstrap import create_app

    app = create_app()
    print(json.dumps(app.status(), ensure_ascii=False, indent=2))
    return 0


def cmd_test_deepseek() -> int:
    from PetAI.app.bootstrap import create_app

    app = create_app()
    try:
        result = app.deepseek.test_connection()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"[test-deepseek] 失败: {exc}", file=sys.stderr)
        return 1


def cmd_tts_status() -> int:
    from PetAI.app.bootstrap import create_app

    app = create_app()
    print(json.dumps(app.tts.capabilities(), ensure_ascii=False, indent=2))
    return 0


def cmd_list_productions() -> int:
    from PetAI.app.bootstrap import create_app

    app = create_app()
    rows = app.video.list_productions()
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="PetAI", description="Pet AI Content Factory 工具")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("init", help="初始化数据库与配置模板")
    sub.add_parser("status", help="查看成本控制中心状态")
    sub.add_parser("test-deepseek", help="测试 DeepSeek 连接")
    sub.add_parser("tts-status", help="查看本地 TTS 能力与许可证")
    sub.add_parser("productions", help="列出视频生产记录")
    args = parser.parse_args()
    return {
        "init": cmd_init,
        "status": cmd_status,
        "test-deepseek": cmd_test_deepseek,
        "tts-status": cmd_tts_status,
        "productions": cmd_list_productions,
    }.get(args.command, lambda: (parser.print_help(), 2)[1])()


if __name__ == "__main__":
    raise SystemExit(main())
