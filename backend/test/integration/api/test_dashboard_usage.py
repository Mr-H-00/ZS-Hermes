"""用量统计的真实 PostgreSQL 和 HTTP 观测边界。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from sqlalchemy import update

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.routers.dashboard import dashboard
from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.models.messages import Message
from yuxi.modules.agents.models.runs import AgentRun
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.models.turns import AgentTurn
from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.system.repositories import dashboard as dashboard_repository
from yuxi.modules.workspace.models import Project

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


def reported_usage(input_tokens, output_tokens, *, complete=True):
    """人工固定的 Provider 上报样本，预期总量由用例独立断言。"""
    return {
        "total": {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens},
        "usage_reported_call_count": 1,
        "complete": complete,
    }


@pytest.mark.parametrize("time_range", ["14hours", "14days", "14weeks"])
async def test_run_usage_preserves_partial_unknown_zero_and_calendar_boundaries(monkeypatch, time_range):
    """旧 Message 用量不能改写 Run 事实，十四桶保留缺报、真实零和首尾边界。"""
    now = datetime(2026, 10, 29, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(dashboard_repository, "utc_now", lambda: now)
    step = {"14hours": timedelta(hours=1), "14days": timedelta(days=1), "14weeks": timedelta(weeks=1)}[time_range]
    start = now.replace(minute=0, second=0, microsecond=0)
    if time_range != "14hours":
        start = start.replace(hour=0)
    if time_range == "14weeks":
        start -= timedelta(days=start.weekday())
    start -= step * 13

    async with isolated_audit_store(monkeypatch) as (_manager, sessions):
        async with sessions() as db:
            department = Department(name="用量测试部门")
            db.add(department)
            await db.flush()
            users = [
                User(
                    uid=f"usage-user-{i}",
                    username=f"usage-user-{i}",
                    password_hash="fixture",
                    department_id=department.id,
                    role="superadmin" if i == 0 else "user",
                    is_deleted=int(i == 2),
                )
                for i in range(3)
            ]
            agent = Agent(slug="usage-agent", name="用量测试", backend_id="chatbot", visibility="shared", share_config={})
            db.add_all([*users, agent])
            await db.flush()
            headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(users[0].id), 'auth_method': 'password'})}"}
            normal_headers = {
                "Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(users[1].id), 'auth_method': 'password'})}"
            }
            projects = [
                Project(
                    id=f"usage-project-{i}", uid=user.uid, selection_status="implicit", workdir_path="test/usage", directory_mode="managed"
                )
                for i, user in enumerate(users)
            ]
            db.add_all(projects)
            await db.flush()
            records = []
            for index in range(6):
                owner_index = 2 if index == 5 else 0
                record = Session(
                    thread_id=f"usage-thread-{index}",
                    uid=users[owner_index].uid,
                    project_id=projects[owner_index].id,
                    agent_id=agent.slug,
                    status="deleted" if index == 4 else "active",
                )
                db.add(record)
                records.append(record)
            await db.flush()

            async def add_run(name, session_index, created, usage, *, previous=None):
                """提交合法 Turn/Run 关联，恢复段保持同一 Turn。"""
                session = records[session_index]
                turn_id = previous.turn_id if previous else f"turn-{name}"
                if previous is None:
                    db.add(AgentTurn(id=turn_id, thread_id=session.thread_id, uid=session.uid, status="completed"))
                    await db.flush()
                run = AgentRun(
                    id=name,
                    session_record_id=session.id,
                    thread_id=session.thread_id,
                    runtime_scope_id=session.thread_id,
                    uid=session.uid,
                    agent_slug=agent.slug,
                    turn_id=turn_id,
                    status="completed",
                    token_usage=usage,
                    created_at=created,
                    run_type="resume" if previous else "chat",
                    resume_from_run_id=previous.id if previous else None,
                )
                db.add(run)
                await db.flush()
                return run

            first = await add_run("usage-first", 0, start, reported_usage(100, 20))
            await add_run("usage-resume", 0, now, reported_usage(50, 30), previous=first)
            await add_run(
                "usage-missing",
                0,
                now,
                {"total": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "complete": False, "usage_reported_call_count": 0},
            )
            await add_run("usage-zero", 1, start + step, reported_usage(0, 0))
            await add_run("usage-unknown", 2, start + step * 2, {"available": False})
            await add_run("usage-before", 3, start - timedelta(microseconds=1), reported_usage(900, 100))
            await add_run("usage-future", 3, now + timedelta(microseconds=1), reported_usage(900, 100))
            await add_run("usage-deleted", 4, now, reported_usage(900, 100))
            await add_run("usage-deleted-user", 5, now, reported_usage(900, 100))
            db.add(
                Message(
                    session_record_id=records[0].id,
                    role="assistant",
                    content="fixture",
                    created_at=now,
                    extra_metadata={"usage_metadata": {"input_tokens": 9000, "output_tokens": 1000}},
                )
            )
            await db.commit()

        app = FastAPI()
        app.include_router(dashboard, prefix="/api")
        async with serve_http(app) as client:
            path = "/api/dashboard/stats/calls/timeseries"
            response = await client.get(path, params={"type": "tokens", "time_range": time_range}, headers=headers)
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_count"] == 200
            assert payload["token_usage_complete"] is False
            assert (payload["run_count"], payload["incomplete_run_count"]) == (5, 2)
            assert len(payload["data"]) == 14
            first_bucket, zero_bucket, unknown_bucket = payload["data"][:3]
            assert first_bucket["data"] == {"input_tokens": 100, "output_tokens": 20}
            assert first_bucket["token_usage_complete"] is True
            assert zero_bucket["data"] == {"input_tokens": 0, "output_tokens": 0}
            assert zero_bucket["token_usage_complete"] is True
            assert unknown_bucket["data"] == {"input_tokens": None, "output_tokens": None}
            assert unknown_bucket["total"] is None
            assert unknown_bucket["token_usage_complete"] is False
            assert payload["data"][-1]["total"] == 80
            assert payload["data"][-1]["token_usage_complete"] is False
            assert payload["data"][3]["total"] == 0 and payload["data"][3]["run_count"] == 0

            detail = await client.get("/api/dashboard/sessions/usage-thread-0", headers=headers)
            assert detail.status_code == 200, detail.text
            assert detail.json()["total_tokens"] == 200
            assert detail.json()["token_usage_complete"] is False
            for index, expected, complete in [(1, 0, True), (2, None, False)]:
                detail = await client.get(f"/api/dashboard/sessions/usage-thread-{index}", headers=headers)
                assert detail.json()["total_tokens"] == expected
                assert detail.json()["token_usage_complete"] is complete
            assert (await client.get(path, params={"type": "tokens"})).status_code == 401
            assert (await client.get(path, params={"type": "tokens"}, headers=normal_headers)).status_code == 403

            summary = await client.get("/api/dashboard/stats/threads", headers=headers)
            assert summary.status_code == 200, summary.text
            assert summary.json()["summary"]["total_tokens"] == 2200
            assert summary.json()["summary"]["token_usage_complete"] is False
            assert summary.json()["agent_distribution"][0]["token_count"] == 2200
            assert summary.json()["agent_distribution"][0]["token_usage_complete"] is False

            included = ["usage-first", "usage-resume", "usage-missing", "usage-zero", "usage-unknown"]
            for usage, expected, complete in [({"available": False}, None, False), (reported_usage(0, 0), 0, True)]:
                async with sessions() as db:
                    await db.execute(update(AgentRun).where(AgentRun.id.in_(included)).values(token_usage=usage))
                    await db.commit()
                response = await client.get(path, params={"type": "tokens", "time_range": time_range}, headers=headers)
                result = response.json()
                assert result["total_count"] == expected
                assert result["peak_count"] == expected
                assert result["token_usage_complete"] is complete
