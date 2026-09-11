from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def run_sync_with_deferred_cancellation(func: Callable[..., Any], /, *args, **kwargs) -> Any:
    """在线程调用取得确定结果后传播等待期间收到的协程取消。"""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result
