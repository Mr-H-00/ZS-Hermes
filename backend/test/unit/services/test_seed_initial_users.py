"""独立脚本不能依赖 API 或测试进程预先注册 ORM 模型。"""

import subprocess
import sys
from pathlib import Path


def test_seed_registers_models_before_schema_gate():
    """在全新进程验证种子入口的完整关系映射，无需连接数据库。"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio
from unittest.mock import patch
from sqlalchemy.orm import configure_mappers
from scripts.seed_initial_users import seed_initial_users

async def check_schema(manager):
    configure_mappers()
    raise RuntimeError("schema-gate-reached")

with patch("yuxi.infrastructure.postgres.manager.pg_manager.initialize"), patch(
    "yuxi.infrastructure.postgres.schema.require_current_schema", side_effect=check_schema
):
    try:
        asyncio.run(seed_initial_users())
    except RuntimeError as exc:
        assert str(exc) == "schema-gate-reached", str(exc)
    else:
        raise AssertionError("seed bypassed the schema gate")
""",
        ],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
