"""SSE 이벤트 순서와 끊김 시 상류 취소 (설계서 8장 통합 테스트, 가짜 LLM)."""

import asyncio
import json
import uuid

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError

from devdocs_agent.api import chat
from devdocs_agent.api.main import app
from devdocs_agent.llm import client, profiles
from tests.fakes import FakeLLM, normal

KEY = "test-key"


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("API_KEY", KEY)
    monkeypatch.setenv("PROFILE", "onprem")
    monkeypatch.setenv("SHOW_REASONING", "false")
    yield TestClient(app)
    app.dependency_overrides.clear()


def use(fake: FakeLLM) -> None:
    app.dependency_overrides[chat.get_stream_chat] = lambda: fake


def parse(text: str) -> list[tuple[str, dict]]:
    """SSE 본문을 (event, data) 목록으로 바꾼다. ping 주석은 버린다."""
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def post(api: TestClient, **body) -> list[tuple[str, dict]]:
    r = api.post("/v1/chat", json={"message": "안녕", **body}, headers={"X-API-Key": KEY})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse(r.text)


def test_event_order(api):
    use(FakeLLM(normal()))
    events = post(api)
    assert [e for e, _ in events] == ["meta", "token", "token", "usage", "done"]
    rid = events[0][1]["request_id"]
    assert all(d["request_id"] == rid for _, d in events)
    assert "".join(d["delta"] for e, d in events if e == "token") == "안녕"


def test_reasoning_shown_when_enabled(api, monkeypatch):
    monkeypatch.setenv("SHOW_REASONING", "true")
    use(FakeLLM(normal()))
    assert [e for e, _ in post(api)] == ["meta", "reasoning", "token", "token", "usage", "done"]


def test_upstream_error_becomes_error_event(api):
    use(FakeLLM([client.Token("안"), RuntimeError("boom")]))
    events = post(api)
    assert [e for e, _ in events] == ["meta", "token", "error"]
    assert events[-1][1]["code"] == "internal_error"


def test_unreachable_upstream_is_labeled(api):
    """Ollama 가 꺼져 있으면 internal_error 가 아니라 upstream_unreachable 로 알린다."""
    down = APIConnectionError(request=httpx2.Request("POST", "http://localhost:11434/v1/chat/completions"))
    use(FakeLLM([down]))
    events = post(api)
    assert [e for e, _ in events] == ["meta", "error"]
    assert events[-1][1]["code"] == "upstream_unreachable"


def test_requires_api_key(api):
    use(FakeLLM(normal()))
    assert api.post("/v1/chat", json={"message": "안녕"}).status_code == 401
    assert api.post("/v1/chat", json={"message": "안녕"}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_unknown_model_is_400(api):
    use(FakeLLM(normal()))
    r = api.post("/v1/chat", json={"message": "안녕", "model": "no-such-model"}, headers={"X-API-Key": KEY})
    assert r.status_code == 400


def test_disconnect_cancels_upstream(monkeypatch):
    """클라이언트가 끊으면(= Starlette 가 relay 생성기를 닫으면) 상류 스트림도 닫힌다."""
    monkeypatch.setenv("PROFILE", "onprem")
    fake = FakeLLM(normal() * 50, delay=0.01)  # 끝까지 가면 0.5초 이상 걸린다
    target = profiles.resolve()

    async def scenario():
        rid = uuid.uuid4()
        gen = chat.relay(fake(target, [], request_id=rid), target, rid)
        await anext(gen)  # meta
        await anext(gen)  # 첫 token
        await gen.aclose()  # 끊김
        await asyncio.sleep(0.05)  # 취소된 상류 태스크가 finally 를 돌 시간
        return fake.closed

    assert asyncio.run(scenario()) is True