# ruff: noqa: E402  # 入口必须先加载环境和事件循环策略。
"""ARQ worker entrypoint."""

import asyncio
import logging.config
import os
import sys

from zs.bootstrap.environment import load_environment

load_environment()

# 必须放在最顶层！
if sys.platform == "win32":
    # 把当前文件 (main.py) 的上一级的上一级 (即根目录 ZS) 加入到 sys.path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from zs.workers.settings import WorkerSettings

__all__ = ["WorkerSettings"]


def main() -> None:
    """沿用 ARQ 默认日志与运行生命周期，装配 ZS 的领取适配。"""
    from arq.logs import default_log_config

    from zs.workers.arq import run_worker

    logging.config.dictConfig(default_log_config(False))
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
