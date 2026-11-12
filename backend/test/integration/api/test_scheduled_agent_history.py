"""定时任务历史通过真实 HTTP 和隔离 PostgreSQL 验证。"""

from datetime import timedelta

import pytest
from fastapi import FastAPI

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.routers.schedules import scheduled_agents
from yuxi.modules.agents.input_messages import build_chat_input_message, serialize_input_message
from yuxi.modules.agents.models.sessions import Session
from yuxi.modules.agents.repositories.input import AgentInputRepository
from yuxi.modules.identity.models import Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.schedules.models import ScheduledAgentJob, ScheduledAgentRun
from yuxi.modules.schedules.repository import ScheduledAgentRepository
from yuxi.modules.workspace.models import Project
from yuxi.shared.datetime import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


async def test_history_pages_all_occurrences_and_enforces_owner(monkeypatch):
    """同时间记录稳定分页，失败、跳过及排队记录保留，其他用户不可读取。"""
    async with isolated_audit_store(monkeypatch) as (_manager, sessions):
        now = utc_now()
        async with sessions() as db:
            department = Department(name="历史测试部门")
            db.add(department)
            await db.flush()
            owner = User(uid="history-owner", username="history-owner", password_hash="fixture", department_id=department.id)
            other = User(uid="history-other", username="history-other", password_hash="fixture", department_id=department.id)
            db.add_all([owner, other])
            await db.flush()
            headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(owner.id), 'auth_method': 'password'})}"}
            other_headers = {"Authorization": f"Bearer {AuthUtils.create_access_token({'sub': str(other.id), 'auth_method': 'password'})}"}
            project = Project(
                id="history-project", uid=owner.uid, selection_status="implicit", workdir_path="test/history", directory_mode="managed"
            )
            db.add(project)
            await db.flush()
            job = ScheduledAgentJob(
                id="history-job",
                uid=owner.uid,
                creation_request_id="history-request",
                creation_intent_hash="0" * 64,
                project_id=project.id,
                agent_slug="chatbot",
                name="历史任务",
                prompt="fixture",
                cron_expression="0 9 * * *",
                timezone="Asia/Shanghai",
                enabled=False,
                next_run_at=now,
            )
            db.add(job)
            await db.flush()
            records = []
            for index in range(25):
                record = ScheduledAgentRun(
                    id=f"history-run-{index:02}",
                    job_id=job.id,
                    input_id=f"history-input-{index}",
                    thread_id=f"history-thread-{index}",
                    occurrence_key=f"history-{index}",
                    scheduled_for=now - timedelta(days=index // 5),
                    project_id=project.id,
                    agent_slug="chatbot",
                    session_title=job.name,
                    prompt="fixture",
                    tool_approval_mode="default",
                    trigger="manual" if index % 2 else "scheduled",
                    status="failed" if index % 2 else "skipped",
                    error_message=f"历史错误 {index}",
                )
                db.add(record)
                records.append(record)
            queued = records[4]
            db.add(Session(thread_id=queued.thread_id, uid=owner.uid, project_id=project.id, agent_id="chatbot"))
            await db.flush()
            await AgentInputRepository(db).create(
                input_id=queued.input_id,
                thread_id=queued.thread_id,
                uid=owner.uid,
                app_id=None,
                agent_slug="chatbot",
                kind="follow_up",
                messages=[serialize_input_message(build_chat_input_message("fixture"))],
                source="scheduled_agent",
                channel="worker",
            )
            queued.status = "submitted"
            queued.error_message = None
            await db.commit()
            expected = [record.id for record in sorted(records, key=lambda record: (record.scheduled_for, record.id), reverse=True)]

        app = FastAPI()
        app.include_router(scheduled_agents, prefix="/api")
        path = "/api/scheduled-tasks/history-job/runs"
        async with serve_http(app) as client:
            pages = []
            for offset in (0, 10, 20):
                response = await client.get(path, params={"limit": 10, "offset": offset}, headers=headers)
                assert response.status_code == 200, response.text
                payload = response.json()
                assert (payload["total"], payload["limit"], payload["offset"]) == (25, 10, offset)
                pages.extend(payload["items"])
            assert [record["id"] for record in pages] == expected
            assert len({record["id"] for record in pages}) == 25
            queued_result = next(record for record in pages if record["id"] == queued.id)
            assert queued_result["status"] == "queued"
            assert queued_result["session_available"] is True
            assert queued_result["thread_id"] == queued.thread_id
            failed = next(record for record in pages if record["id"] == "history-run-01")
            assert (failed["status"], failed["error_message"], failed["session_available"]) == ("failed", "历史错误 1", False)
            assert (await client.get(path, params={"offset": 100}, headers=headers)).json()["items"] == []
            assert (await client.get(path, headers=other_headers)).status_code == 404
            async with sessions() as db:
                rows, total = await ScheduledAgentRepository(db).list_run_history("history-job", other.uid, limit=10, offset=0)
                assert rows == [] and total == 0
            assert (await client.get(path.replace("history-job", "missing-job"), headers=headers)).status_code == 404
            assert (await client.get(path)).status_code == 401
            for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
                assert (await client.get(path, params=params, headers=headers)).status_code == 422
            async with sessions() as db:
                saved = await db.get(ScheduledAgentJob, "history-job")
                saved.deleted_at = now
                await db.commit()
            assert (await client.get(path, headers=headers)).status_code == 404
