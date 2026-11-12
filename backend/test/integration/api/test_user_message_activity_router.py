"""GET /api/user/message-activity 的 HTTP 契约。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

MESSAGE_ACTIVITY_PATH = "/api/user/message-activity"


async def test_message_activity_requires_auth(test_client: httpx.AsyncClient):
    response = await test_client.get(MESSAGE_ACTIVITY_PATH)
    assert response.status_code == 401


async def test_message_activity_returns_filled_window_for_fresh_user(test_client, standard_user):
    response = await test_client.get(MESSAGE_ACTIVITY_PATH, headers=standard_user["headers"])
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["window_days"] == 365
    days = payload["daily_message_counts"]
    assert len(days) == 365

    dates = [datetime.strptime(item["date"], "%Y-%m-%d").date() for item in days]
    assert len(set(dates)) == 365
    assert all((later - earlier).days == 1 for earlier, later in zip(dates, dates[1:]))
    # 容忍与本地"今天"计算之间的午夜瞬间竞争。
    shanghai_today = datetime.now(timezone(timedelta(hours=8))).date()
    assert dates[-1] in {shanghai_today, shanghai_today - timedelta(days=1)}

    # 新建用户没有任何会话；其余用户消息不计入的负向语义由
    # test/unit/repositories/test_message_daily_activity.py 的跨用户案例承担。
    assert all(item["message_count"] == 0 for item in days)
