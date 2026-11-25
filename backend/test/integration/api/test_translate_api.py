"""经真实 HTTP、认证和 PostgreSQL 验证独立翻译及模型调用。"""

import sys
import textwrap
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import uuid4

import httpx
import pytest
from docx import Document
from fastapi import FastAPI, Request
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import update
from sqlalchemy.pool import AsyncAdaptedQueuePool

from test.integration.support.module_audit import isolated_audit_store, serve_http
from yuxi.api.main import app
from yuxi.infrastructure.redis import sync_redis_client
from yuxi.modules.identity.models import APIKey, Department, User
from yuxi.modules.identity.security import AuthUtils
from yuxi.modules.models.providers import cache
from yuxi.modules.models.tables import ModelProvider

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


def _pdf_text_fixture(value: bytes) -> bytes:
    """生成独立的可搜索 PDF 测试输入。"""
    pdf = PdfWriter()
    page = pdf.add_blank_page(width=300, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 14 Tf 20 90 Td (" + value + b") Tj ET")
    page.replace_contents(stream.flate_encode())
    data = BytesIO()
    pdf.write(data)
    return data.getvalue()


async def test_translate_api_observes_authenticated_provider_and_wire(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """验证普通用户的模型请求、译文及失效模型的拒绝边界。"""
    observed: list[tuple[dict, str | None]] = []
    checked_out_connections: list[int] = []
    upstream = FastAPI()

    @upstream.post("/v1/chat/completions")
    async def completion(request: Request) -> dict:
        """发送独立固定语言结果，捕获供应商真实入站协议。"""
        payload = await request.json()
        observed.append((payload, request.headers.get("authorization")))
        checked_out_connections.append(pool.checkedout())
        instruction = payload["messages"][0]["content"]
        if "判断用户原文自身的语言" in instruction:
            translated = "EN" if "forest" in payload["messages"][-1]["content"] else "unexpected-code"
        elif "日语" in instruction:
            translated = "森は静かです。"
        else:
            translated = "森林很安静。"
        return {
            "id": "chatcmpl-translation",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": translated}, "finish_reason": "stop"}],
        }

    cache_key = f"pytest:translation:{uuid4().hex}"
    monkeypatch.setattr(cache, "REDIS_CACHE_KEY", cache_key)
    monkeypatch.setattr(cache.model_cache, "_local_cache", None)
    monkeypatch.setattr(cache.model_cache, "_local_cache_at", 0.0)
    monkeypatch.setenv("RUNNING_IN_DOCKER", "false")
    source = "The forest is quiet."
    payload = {"text": source, "source_language": "en", "target_language": "zh", "model_spec": "fixture:fixture-chat"}

    async with isolated_audit_store(monkeypatch) as (manager, sessions), serve_http(upstream) as provider_http:
        assert manager.async_engine is not None
        pool = cast(AsyncAdaptedQueuePool, manager.async_engine.sync_engine.pool)
        async with sessions() as db:
            department = Department(name="Translation fixture")
            db.add(department)
            await db.flush()
            user = User(
                uid="translation-user", username="translation-user", role="user", password_hash="unused", department_id=department.id
            )
            provider = ModelProvider(
                provider_id="fixture",
                display_name="Fixture translation provider",
                base_url=f"{str(provider_http.base_url).rstrip('/')}/v1",
                api_key="fixture-provider-key",
                capabilities=["chat", "embedding"],
                enabled_models=[
                    {"id": "fixture-chat", "type": "chat"},
                    {"id": "fixture-embedding", "type": "embedding"},
                ],
            )
            db.add_all([user, provider])
            await db.flush()
            token = AuthUtils.create_access_token({"sub": str(user.id), "auth_method": "password"})
            secret, key_hash, key_prefix = AuthUtils.generate_api_key()
            db.add(
                APIKey(
                    key_hash=key_hash,
                    key_prefix=key_prefix,
                    name="Translation fixture key",
                    access_level="full",
                    user_id=user.id,
                    created_by=user.uid,
                )
            )
            await db.commit()
            cache.model_cache.rebuild([provider])

        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with serve_http(app) as client:
                unauthenticated = await client.post("/api/translate", json=payload)
                assert unauthenticated.status_code == 401

                denied_key = await client.post("/api/translate", json=payload, headers={"Authorization": f"Bearer {secret}"})
                assert denied_key.status_code == 403
                assert denied_key.json()["detail"] == "翻译仅支持 Web 登录用户"

                explicit_uid = await client.post("/api/translate", json={**payload, "uid": "other-user"}, headers=headers)
                assert explicit_uid.status_code == 422

                non_chat = await client.post("/api/translate", json={**payload, "model_spec": "fixture:fixture-embedding"}, headers=headers)
                assert non_chat.status_code == 503
                assert non_chat.json()["detail"]["code"] == "translation_model_unavailable"
                assert observed == []

                first = await client.post("/api/translate", json=payload, headers=headers)
                second = await client.post(
                    "/api/translate", json={**payload, "source_language": "auto", "target_language": "ja"}, headers=headers
                )
                assert first.status_code == second.status_code == httpx.codes.OK
                assert first.headers["cache-control"] == second.headers["cache-control"] == "no-store"
                assert first.json() == {"translated_text": "森林很安静。"}
                assert second.json() == {"translated_text": "森は静かです。"}
                assert checked_out_connections == [0, 0]
                expected_instructions = [("英语", "简体中文"), ("自动识别", "日语")]
                for (request, auth), (source_label, target_label) in zip(observed, expected_instructions, strict=True):
                    assert auth == "Bearer fixture-provider-key"
                    assert request["model"] == "fixture-chat"
                    instructions, original = request["messages"]
                    assert instructions["role"] == "system"
                    assert source_label in instructions["content"] and target_label in instructions["content"]
                    assert source not in instructions["content"]
                    assert original == {"role": "user", "content": source}

                translated_with_settings = await client.post(
                    "/api/translate",
                    json={
                        **payload,
                        "target_language": "custom",
                        "target_language_name": "自定义语言",
                        "prompt": "目标语言={{target_language}}，源语言={{source_language}}，原文={{text}}",
                        "temperature": 0.4,
                        "top_p": 0.7,
                        "reasoning_effort": "low",
                    },
                    headers=headers,
                )
                assert translated_with_settings.json() == {"translated_text": "森林很安静。"}
                replacement, _ = observed[-1]
                assert replacement["temperature"] == 0.4
                assert replacement["top_p"] == 0.7
                assert replacement["reasoning_effort"] == "low"
                instruction, original = replacement["messages"]
                assert "目标语言=自定义语言，源语言=英语" in instruction["content"]
                assert source not in instruction["content"]
                assert original["content"] == source

                invalid_settings = await client.post("/api/translate", json={**payload, "temperature": 3}, headers=headers)
                assert invalid_settings.status_code == 422
                assert len(observed) == 3

                languages = [{"code": "en", "name": "英语"}, {"code": "zh", "name": "简体中文"}]
                detection = {
                    "text": source,
                    "model_spec": payload["model_spec"],
                    "languages": languages,
                }
                denied_detect = await client.post("/api/translate/detect", json=detection)
                duplicate = await client.post("/api/translate/detect", json={**detection, "languages": languages * 2}, headers=headers)
                detected = await client.post("/api/translate/detect", json=detection, headers=headers)
                unknown = await client.post("/api/translate/detect", json={**detection, "text": "123"}, headers=headers)
                assert denied_detect.status_code == 401
                assert duplicate.status_code == 422
                assert detected.json() == {"source_language": "en"}
                assert unknown.json() == {"source_language": None}
                assert checked_out_connections == [0, 0, 0, 0, 0]

                file = {"file": ("中文.txt", "真实文本\n第二行".encode("utf-16"), "text/plain")}
                denied_upload = await client.post("/api/translate/extract", files=file)
                assert denied_upload.status_code == 401
                docx = Document()
                docx.add_paragraph("Word body")
                rendered = BytesIO()
                docx.save(rendered)
                for files, expected in [
                    (file, "真实文本\n第二行"),
                    ({"file": ("searchable.pdf", _pdf_text_fixture(b"PDF body"), "application/pdf")}, "PDF body"),
                    ({"file": ("word.docx", rendered.getvalue())}, "Word body"),
                    ({"file": ("sheet.csv", b"City,Value\nParis,3\n", "text/csv")}, "Paris"),
                ]:
                    extracted = await client.post("/api/translate/extract", files=files, headers=headers, timeout=120)
                    assert extracted.status_code == 200
                    assert extracted.headers["cache-control"] == "no-store"
                    assert expected in extracted.json()["text"]

                unsupported = await client.post("/api/translate/extract", files={"file": ("test.exe", b"sample")}, headers=headers)
                assert unsupported.status_code == 422
                assert unsupported.json()["detail"]["code"] == "translation_file_unsupported"
                corrupt = await client.post("/api/translate/extract", files={"file": ("broken.pdf", b"invalid pdf")}, headers=headers)
                assert corrupt.status_code == 422
                assert corrupt.json()["detail"]["code"] == "translation_file_invalid"
                oversized = await client.post("/api/translate/extract", files={"file": ("test.txt", b"t" * 10001)}, headers=headers)
                assert oversized.status_code == 413
                assert oversized.json()["detail"]["code"] == "translation_text_too_long"

                await _check_pdf_layout(client, headers, monkeypatch, tmp_path)
                assert len(observed) == 7
                assert checked_out_connections[-1] == 0
                assert observed[-1][0]["messages"][-1] == {"role": "user", "content": "Translate the PDF paragraph."}

                async with sessions() as db:
                    await db.execute(update(ModelProvider).where(ModelProvider.provider_id == "fixture").values(is_enabled=False))
                    await db.commit()

                disabled = await client.post("/api/translate", json=payload, headers=headers)
                assert disabled.status_code == 503
                assert disabled.json()["detail"]["code"] == "translation_model_unavailable"
                assert len(observed) == 7
        finally:
            with sync_redis_client() as redis:
                redis.delete(cache_key)


async def _check_pdf_layout(client: httpx.AsyncClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """验证真实 HTTP、CLI 进程、当前供应商和译文下载的完整路径。"""
    engine = tmp_path / "babeldoc-fixture"
    reference = tmp_path / "translated.pdf"
    reference.write_bytes(_pdf_text_fixture(b"Finished layout"))
    fields = {"model_spec": "fixture:fixture-chat", "source_language": "en", "target_language": "zh"}
    file = {"file": ("原文.pdf", _pdf_text_fixture(b"Original PDF"), "application/pdf")}
    monkeypatch.setenv("YUXI_BABELDOC_COMMAND", str(engine))
    missing = await client.get("/api/translate/pdf/capability", headers=headers)
    assert missing.json() == {"available": False}
    unavailable = await client.post("/api/translate/pdf", data=fields, files=file, headers=headers)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == "translation_pdf_unavailable"

    engine.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """
            import argparse
            import json
            import os
            import shutil
            import sys
            import tomllib
            import urllib.error
            import urllib.request
            from pathlib import Path

            reference = Path(sys.argv[0]).with_name("translated.pdf")
            parser = argparse.ArgumentParser(allow_abbrev=False)
            parser.add_argument("--openai", action="store_true", required=True)
            for name in ("config", "files", "output", "openai-base-url", "openai-model", "lang-in", "lang-out"):
                parser.add_argument("--" + name, required=True)
            args, options = parser.parse_known_args()
            assert "--ignore-cache" in options and args.lang_in == "en" and args.lang_out == "zh-CN"
            assert Path(args.config).stat().st_mode & 0o777 == 0o600
            assert not os.environ.get("OPENAI_API_KEY")
            assert Path(args.files).name == "source.pdf"
            result = Path(args.output) / "finished.mono.pdf"
            token = tomllib.loads(Path(args.config).read_text())["babeldoc"]["openai-api-key"]
            assert token != "fixture-provider-key"
            url = args.openai_base_url + "/chat/completions"
            payload = {"model": args.openai_model, "messages": [{"role": "user", "content": "Translate the PDF paragraph."}]}
            request = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(request)
            except urllib.error.HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("missing gateway auth was accepted")
            request.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(request) as response:
                answer = json.load(response)["choices"][0]["message"]["content"]
            assert answer == "\u68ee\u6797\u5f88\u5b89\u9759\u3002"
            if reference.with_suffix(".outside").exists():
                result.symlink_to(reference)
            else:
                shutil.copyfile(reference, result)
            print(json.dumps({"schema": "babeldoc-stream/v2", "type": "finish", "result": {"mono_pdf_path": str(result)}}))
            """
        ),
        encoding="utf-8",
    )
    engine.chmod(0o700)
    available = await client.get("/api/translate/pdf/capability", headers=headers)
    assert available.json() == {"available": True}
    rejected = await client.post("/api/translate/pdf", data=fields, files=file)
    assert rejected.status_code == 401
    response = await client.post("/api/translate/pdf", data=fields, files=file, headers=headers, timeout=120)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''%E5%8E%9F%E6%96%87-translated.pdf"
    assert response.content == reference.read_bytes()

    reference.with_suffix(".outside").touch()
    escaped = await client.post("/api/translate/pdf", data=fields, files=file, headers=headers, timeout=120)
    assert escaped.status_code == 502, escaped.text
    assert escaped.json()["detail"]["code"] == "translation_pdf_failed"
