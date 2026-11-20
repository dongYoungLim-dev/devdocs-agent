"""POST /v1/chat. M1 은 검색 없이 LLM 답변만 SSE 로 흘린다."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openai import APIStatusError, OpenAIError
from pydantic import BaseModel

from devdocs_agent.api import sse
from devdocs_agent.api.auth import require_key
from devdocs_agent.llm import client, profiles

router = APIRouter()
PING_SEC = 15


class ChatIn(BaseModel):
    message: str
    mode: Literal["rag", "agent"] = "rag"  # M1 은 둘 다 검색·도구 없이 LLM 만 부른다
    profile: str | None = None
    model: str | None = None


def get_stream_chat():
    """테스트에서 가짜 LLM 으로 바꿔 끼우는 자리(app.dependency_overrides)."""
    return client.stream_chat


@router.post("/v1/chat", dependencies=[Depends(require_key)])
async def chat(
        body: ChatIn, stream_chat: Annotated[Callable, Depends(get_stream_chat)]
) -> StreamingResponse:
    try:
        target = profiles.resolve(body.profile, body.model)
    except LookupError as e:
        raise HTTPException(400, str(e)) from e
    rid = uuid.uuid4()
    upstream = stream_chat(target, [{"role": "user", "content": body.message}], request_id=rid, mode=body.mode)
    return StreamingResponse(
        relay(upstream, target, rid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def error_data(e: Exception) -> dict:
    """SSE error 이벤트 내용. 누구 탓인지(code)를 먼저 나눈다."""
    if isinstance(e, APIStatusError):  # 상류 LLM 이 오류 응답을 보냈다(4xx, 5xx)
        return {"code": "upstream_error", "status": e.status_code, "message": str(e)[:300]}
    if isinstance(e, OpenAIError):  # 상류에 닿지 못했다(Ollama 꺼짐, 시간 초과 등)
        return {"code": "upstream_unreachable", "message": type(e).__name__}
    return {"code": "internal_error", "message": type(e).__name__}  # 우리 코드의 오류. 내부 사정은 내보내지 않는다


async def relay(upstream: AsyncIterator, target: profiles.Target, rid: uuid.UUID) -> AsyncIterator[str]:
    """상류 이벤트를 SSE 로 바꾼다. 순서: meta → reasoning*/token* → usage → done (실패면 meta → error).

    상류는 별도 태스크가 읽어 큐에 넣는다. 그래야 생각이 길어 15초 동안 아무것도 안 와도 ping 을 보낼 수 있다.
    클라이언트가 끊으면 Starlette 가 이 생성기를 취소하고, finally 에서 상류 태스크를 취소한다.
    그러면 client.stream_chat 의 finally 가 상류 HTTP 스트림을 닫아 모델이 생성을 멈춘다.
    """
    show_reasoning = os.getenv("SHOW_REASONING", "false").lower() == "true"
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for ev in upstream:
                queue.put_nowait(ev)
        except Exception as e:  # noqa: BLE001 - 어떤 오류든 큐로 넘겨 SSE error 이벤트로 바꾼다
            queue.put_nowait(e)
        finally:
            queue.put_nowait(None)  # 끝 표시

    task = asyncio.create_task(pump())
    base = {"request_id": str(rid)}
    try:
        yield sse.event("meta", {**base, "profile": target.profile, "model": target.model})
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), PING_SEC)
            except TimeoutError:
                yield sse.PING
                continue
            if ev is None:
                break
            if isinstance(ev, Exception):
                yield sse.event("error", {**base, **error_data(ev)})
                return
            if isinstance(ev, client.Reasoning):
                if show_reasoning:  # 끄면 보내지 않고 개수만 usage.reasoning_tokens 로 센다
                    yield sse.event("reasoning", {**base, "delta": ev.delta})
            elif isinstance(ev, client.Token):
                yield sse.event("token", {**base, "delta": ev.delta})
            elif isinstance(ev, client.Done):
                yield sse.event("usage", {**base, **ev.usage})
        yield sse.event("done", base)
    finally:
        task.cancel()