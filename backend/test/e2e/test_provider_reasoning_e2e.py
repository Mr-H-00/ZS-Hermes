"""显式选用真实模型，核对 API→worker→SSE→PostgreSQL→历史的推理一致性。"""

import asyncio
import json
import os
from uuid import uuid4

import asyncpg
import pytest
from test.e2e.e2e_helpers import wait_for_consumed_input
from e2e_helpers import (
    RUN_TIMEOUT_SECONDS,
    archive_public_thread,
    delete_agent,
    iter_public_thread_events,
    postgres_dsn,
)

from test.live_api_cleanup import make_test_session_title

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow]


async def test_reasoning_stream_matches_persisted_history(e2e_client, e2e_headers, e2e_agent_context):
    """无工具的单次编码问答完成后，SSE 原文必须等于同一 Run 的持久化推理。"""
    spec = os.getenv("YUXI_REASONING_E2E_MODEL")
    if not spec:
        pytest.skip("显式配置 YUXI_REASONING_E2E_MODEL 后才调用真实模型")
    client, headers = e2e_client, e2e_headers
    slug = f"e2e-async-agent-reasoning-{uuid4().hex[:8]}"
    response = await client.post(
        "/api/agent",
        headers=headers,
        json={
            "name": "E2E 推理展示测试",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "visibility": "shared",
            "config_json": {
                "context": {
                    "model": spec,
                    "system_prompt": "You review Python code. Answer concisely. Do not use tools.",
                    "tools": [],
                    "knowledges": [],
                    "mcps": [],
                    "skills": [],
                }
            },
            "share_config": {
                "version": 2,
                "read_scope": {
                    "access_level": "user",
                    "department_ids": [],
                    "user_uids": [e2e_agent_context["uid"]],
                },
                "manage_scope": None,
            },
        },
    )
    assert response.status_code == 200
    thread_id = run_id = turn_id = None
    try:
        response = await client.post(
            "/api/v1/agents/sessions",
            headers={**headers, "Idempotency-Key": f"reasoning-create-{uuid4().hex}"},
            json={
                "agent_id": slug,
                "title": make_test_session_title("reasoning-e2e"),
            },
        )
        assert response.status_code == 201, response.text
        thread_id = response.json()["id"]
        response = await client.post(
            f"/api/v1/agents/sessions/{thread_id}/events",
            headers={**headers, "Idempotency-Key": f"reasoning-input-{uuid4().hex}"},
            json={
                "events": [
                    {
                        "type": "agent.session.input.message",
                        "input": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "Review def double(x): return x + 1. Is it correct for doubling all integers? Give a counterexample and the corrected return statement."
                                        ),
                                    }
                                ],
                            }
                        ],
                        "yuxi": {"mode": "follow_up"},
                    }
                ],
            },
        )
        assert response.status_code == 202, response.text
        consumed = await wait_for_consumed_input(client, headers, response.json())
        run_id, turn_id = consumed["run_id"], consumed["turn_id"]

        async def collect_reasoning() -> list[str]:
            """只收集本 Run 的 Public SSE 推理增量。"""
            parts: list[str] = []
            async for event in iter_public_thread_events(client, headers, thread_id):
                if event["type"] == "yuxi.session.turn.reasoning.delta" and event.get("yuxi", {}).get("run_id") == run_id:
                    parts.append(event["delta"])
                if event["turn_id"] == turn_id and event["type"] in {
                    "agent.session.turn.completed",
                    "agent.session.turn.failed",
                    "agent.session.turn.cancelled",
                }:
                    return parts
            pytest.fail("推理 Thread SSE 在终态前断开")

        reasoning_parts = await asyncio.wait_for(collect_reasoning(), timeout=RUN_TIMEOUT_SECONDS)
        turn_response = await client.get(f"/api/v1/agents/sessions/{thread_id}/turns/{turn_id}", headers=headers)
        assert turn_response.status_code == 200, turn_response.text
        turn = turn_response.json()
        assert turn["status"] == "completed", turn
        assert turn["yuxi"]["result_run_id"] == run_id, turn
        reasoning = "".join(reasoning_parts)
        history = await client.get(f"/api/v1/agents/sessions/{thread_id}/items?order=asc&limit=100", headers=headers)
        assert history.status_code == 200
        messages = [
            m for m in history.json()["data"] if m["type"] == "message" and m["role"] == "assistant" and m["yuxi"]["run_id"] == run_id
        ]
        assert len(messages) == 1
        assert "".join(messages[0]["yuxi"].get("reasoning", {}).values()) == reasoning
        assert "".join(part["text"] for part in messages[0]["content"]).strip()
        conn = await asyncpg.connect(postgres_dsn())
        try:
            row = await conn.fetchrow(
                "SELECT content, extra_metadata FROM messages WHERE id=$1 AND run_id=$2",
                messages[0]["yuxi"]["message_id"],
                run_id,
            )
            metadata = json.loads(row["extra_metadata"]) if isinstance(row["extra_metadata"], str) else row["extra_metadata"]
            blocks = metadata["content"]
            assert isinstance(blocks, list)
            assert "".join(b["reasoning"] for b in blocks if b["type"] == "reasoning") == reasoning
            assert "reasoning_content" not in (metadata.get("additional_kwargs") or {})
            assert row["content"] == "".join(part["text"] for part in messages[0]["content"])
        finally:
            await conn.close()
        print(json.dumps({"model": spec, "sse_history_pg_equal": True, "reasoning_chars": len(reasoning)}))
    finally:
        if thread_id:
            await archive_public_thread(client, headers, thread_id, turn_id=turn_id)
        await delete_agent(client, headers, slug)
