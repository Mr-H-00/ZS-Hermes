"""真实 worker 使用已有历史工具读取标题引用的对话。"""

import asyncio
import json
import re
import uuid
from http.server import BaseHTTPRequestHandler

import asyncpg
import pytest

from test.e2e.e2e_helpers import (
    archive_public_thread,
    delete_agent,
    output_text,
    postgres_dsn,
    register_replay_provider,
    wait_for_consumed_input,
    wait_for_terminal_turn,
)
from test.live_api_cleanup import make_test_session_title
from test.support.openai_replay_server import send_openai_sse_response, serve_replay_handler

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(180)]


class SessionMentionReplayHandler(BaseHTTPRequestHandler):
    """仅替代模型供应商，读取结果必须来自真实历史工具。"""

    def log_message(self, *_args):
        """测试模型不记录请求正文。"""

    def do_POST(self):
        """识别稳定引用 ID，工具返回后复述实际读取的用户消息。"""
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = request["messages"]
        user_text = " ".join(str(message["content"]) for message in messages if message["role"] == "user")
        reference = re.search(r'@session:"([^ ]+) ', user_text)
        results = [message for message in messages if message["role"] == "tool"]
        if reference and not results:
            names = {tool["function"]["name"] for tool in request.get("tools", [])}
            assert "read_thread_messages" in names
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-session-history",
                        "type": "function",
                        "function": {"name": "read_thread_messages", "arguments": json.dumps({"thread_id": reference.group(1)})},
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            content = "资料已记录。"
            if reference:
                history = json.loads(results[-1]["content"])
                content = "读取到的对话资料：" + next(item["content"] for item in history["messages"] if item["role"] == "user")
            delta = {"role": "assistant", "content": content}
            finish_reason = "stop"
        common = {"id": "chatcmpl-session-mention", "object": "chat.completion.chunk", "model": request["model"]}
        send_openai_sse_response(
            self,
            [
                {**common, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {**common, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]},
            ],
        )


async def test_worker_reads_selected_session_using_existing_history_tool(e2e_client, e2e_headers):
    """引用经持久消息传给 worker，工具结果正确且自动标题不携带协议寻址串。"""
    provider_id = f"ci-session-{uuid.uuid4().hex[:8]}"
    slug = f"ci-session-{uuid.uuid4().hex[:8]}"
    source_title = make_test_session_title("mention-source")
    fact = f"REFERENCE_FACT_{uuid.uuid4().hex}"
    created_sessions = []
    agent_created = False
    with serve_replay_handler(SessionMentionReplayHandler) as replay_url:
        await register_replay_provider(e2e_client, e2e_headers, base_url=replay_url, provider_id=provider_id)
        try:
            agent = await e2e_client.post(
                "/api/agent",
                headers=e2e_headers,
                json={
                    "name": "对话引用验证",
                    "slug": slug,
                    "backend_id": "ChatbotAgent",
                    "visibility": "private",
                    "config_json": {
                        "context": {"model": f"{provider_id}:deterministic-chat", "tools": [], "skills": [], "mcps": [], "knowledges": []}
                    },
                },
            )
            assert agent.status_code == 200, agent.text
            agent_created = True
            for title, content in [(source_title, fact), (None, None)]:
                if content is None:
                    candidates = await e2e_client.get("/api/mention/sessions", params={"query": source_title}, headers=e2e_headers)
                    assert candidates.status_code == 200, candidates.text
                    [selected] = candidates.json()
                    assert selected["thread_id"] == created_sessions[0][0]
                    content = (
                        "@session:"
                        + json.dumps(f"{selected['thread_id']} {selected['title']}", ensure_ascii=False)
                        + " 请读取这段对话的资料。"
                    )
                    assert fact not in content
                response = await e2e_client.post(
                    "/api/v1/agents/sessions",
                    headers={**e2e_headers, "Idempotency-Key": str(uuid.uuid4())},
                    json={"agent_id": slug, "input": content, "title": title},
                )
                assert response.status_code == 201, response.text
                session_id = response.json()["id"]
                created_sessions.append((session_id, None))
                consumed = await wait_for_consumed_input(e2e_client, e2e_headers, response.json()["yuxi"]["receipt"])
                created_sessions[-1] = (session_id, consumed["turn_id"])
                turn = await wait_for_terminal_turn(e2e_client, e2e_headers, session_id, consumed["turn_id"])
                assert turn["status"] == "completed", turn
            assert fact in output_text(turn["yuxi"]["output"])
            connection = await asyncpg.connect(postgres_dsn())
            try:
                async with asyncio.timeout(15):
                    while True:
                        session = await e2e_client.get(f"/api/v1/agents/sessions/{created_sessions[-1][0]}", headers=e2e_headers)
                        assert session.status_code == 200, session.text
                        title = session.json()["yuxi"]["title"]
                        if title and title not in {"新对话", "New Session"}:
                            break
                        await asyncio.sleep(0.1)
                assert "@session:" not in title and created_sessions[0][0] not in title
                persisted_title = await connection.fetchval("SELECT title FROM sessions WHERE thread_id=$1", created_sessions[-1][0])
                assert persisted_title and persisted_title not in {"新对话", "New Session"}
                assert "@session:" not in persisted_title and created_sessions[0][0] not in persisted_title
                rows = await connection.fetch(
                    "SELECT role, content, extra_metadata FROM messages WHERE run_id=$1 ORDER BY id", turn["yuxi"]["result_run_id"]
                )
                assert any(
                    row["role"] == "user" and created_sessions[0][0] in row["content"] and fact not in row["content"] for row in rows
                )
                assert any(row["role"] == "assistant" and fact in row["content"] for row in rows)
                assert any("read_thread_messages" in row["extra_metadata"] and fact in row["extra_metadata"] for row in rows)
            finally:
                await connection.close()
        finally:
            for session_id, turn_id in reversed(created_sessions):
                await archive_public_thread(e2e_client, e2e_headers, session_id, turn_id=turn_id)
            if agent_created:
                await delete_agent(e2e_client, e2e_headers, slug)
            await e2e_client.delete(f"/api/system/model-providers/{provider_id}", headers=e2e_headers)
