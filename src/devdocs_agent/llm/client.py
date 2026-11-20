"""LLM 호출은 이 파일 한 곳에서만 한다. 호출마다 requests 테이블에 1행을 남긴다."""

import asyncio
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anyio
from openai import AsyncOpenAI, OpenAIError

from devdocs_agent.db import repo
from devdocs_agent.llm.metrics import Meter
from devdocs_agent.llm.profiles import Target


@dataclass
class Reasoning:
    delta: str


@dataclass
class Token:
    delta: str


@dataclass
class Done:
    usage: dict  # Meter.summary(). SSE usage 이벤트에 그대로 쓴다


_clients: dict[tuple[str, str], AsyncOpenAI] = {}
_last_call: dict[str, float] = {}
_pace_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _client(t: Target) -> AsyncOpenAI:
    key = (t.base_url, t.api_key)
    if key not in _clients:
        # SDK 자동 재시도는 끈다. 재시도가 섞이면 TTFT 가 부풀고, 실패도 결과로 남겨야 한다
        _clients[key] = AsyncOpenAI(base_url=t.base_url, api_key=t.api_key, timeout=300, max_retries=0)
    return _clients[key]


async def _pace(t: Target) -> None:
    """models.yaml 에 rpm 이 있는 모델만 요청 사이를 60/rpm 초 띄운다(M0 smoke 의 Pacer 와 같은 규칙)."""
    rpm = t.spec.get("rpm")
    if not rpm:
        return
    async with _pace_locks[t.model]:
        delay = _last_call.get(t.model, 0) + 60 / rpm - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _last_call[t.model] = time.monotonic()


async def stream_chat(
        t: Target, messages: list[dict], *, request_id: uuid.UUID, mode: str = "rag"
) -> AsyncIterator[Reasoning | Token | Done]:
    """스트리밍 호출 1회. 생각 토큰과 답변 토큰을 나눠 내보내고, 마지막에 Done(usage) 을 낸다."""
    body = {"model": t.model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
    for k in ("temperature", "max_tokens"):  # null 이면 요청에 넣지 않는다
        if t.spec.get(k) is not None:
            body[k] = t.spec[k]
    field = t.spec.get("reasoning_field")
    await _pace(t)  # 요청 간격 대기는 시계를 켜기 전에 한다. 기다린 시간이 TTFT 에 섞이지 않게
    meter = Meter(request_id, t.profile, t.model, t.spec, mode)
    stream = None
    try:
        stream = await _client(t).chat.completions.create(**body)
        async for chunk in stream:
            if chunk.usage:
                meter.usage = chunk.usage.model_dump(exclude_none=True)
            if not chunk.choices:
                continue  # include_usage 의 마지막 청크는 choices 가 비어 있다
            choice = chunk.choices[0]
            if choice.finish_reason:
                meter.finish_reason = choice.finish_reason
            # 생각 토큰 필드는 SDK 정의에 없어서 속성으로 붙어 온다(extra="allow"). Gemini 는 field 가 null
            if field and (text := getattr(choice.delta, field, None)):
                meter.mark_reasoning()
                yield Reasoning(text)
            if choice.delta.content:
                meter.mark_content()
                yield Token(choice.delta.content)
        meter.status = "ok"
        yield Done(meter.summary())
    except OpenAIError as e:
        meter.fail(e)
        raise
    finally:
        # 취소(클라이언트 끊김)로 들어와도 상류 스트림을 닫고 행을 남긴다. shield 가 없으면 이 await 들도 취소된다
        with anyio.CancelScope(shield=True):
            if stream is not None:
                await stream.close()
            await repo.insert_request(meter.to_row())