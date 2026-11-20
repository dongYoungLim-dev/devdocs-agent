"""요청 1건의 시간·토큰·비용을 재고 requests 행으로 만든다."""

import json
import time
import uuid

from openai import APIStatusError, OpenAIError

from devdocs_agent.llm.pricing import cost_usd


class Meter:
    def __init__(self, request_id: uuid.UUID, profile: str, model: str, spec: dict, mode: str):
        self.row = {"id": request_id, "profile": profile, "model": model, "mode": mode, "step": 1}
        self.spec = spec
        self.t0 = time.perf_counter()  # 시계 시작. client.py 가 요청을 보내기 직전에 Meter 를 만든다
        self.first_ms: int | None = None  # 첫 델타(생각 포함)
        self.first_content_ms: int | None = None  # 첫 답변 토큰
        self.end_ms: int | None = None  # 끝난 시각. 한 번 정하면 바꾸지 않는다
        self.reasoning_chunks = 0
        self.content_chunks = 0  # 답 델타 개수. tps 를 잴 수 있는지 판단할 때 쓴다
        self.usage: dict = {}
        self.finish_reason: str | None = None
        self.status = "cancelled"  # 끝까지 못 가면 이 값으로 남는다. 정상 종료는 ok, 오류는 error
        self.error: str | None = None

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)

    def stop(self) -> None:
        """끝난 시각을 고정한다. summary() 를 두 번 불러도(usage 이벤트, requests 행) 같은 total_ms 가 나온다."""
        if self.end_ms is None:
            self.end_ms = self.elapsed_ms()

    def mark_reasoning(self) -> None:
        self.reasoning_chunks += 1
        if self.first_ms is None:
            self.first_ms = self.elapsed_ms()

    def mark_content(self) -> None:
        self.content_chunks += 1
        now = self.elapsed_ms()
        if self.first_ms is None:
            self.first_ms = now
        if self.first_content_ms is None:
            self.first_content_ms = now

    def fail(self, e: OpenAIError) -> None:
        self.status = "error"
        info = {"type": type(e).__name__, "message": str(e)}
        if isinstance(e, APIStatusError):  # M0 Gemini 503 처럼 본문이 메시지에 안 붙는 경우가 있어 따로 남긴다
            info["status"] = e.status_code
            info["body"] = e.body if e.body not in (None, "") else e.response.text[:2000]
        self.error = json.dumps(info, ensure_ascii=False, default=str)[:4000]

    def summary(self) -> dict:
        """SSE usage 이벤트와 requests 행에 같이 쓰는 값."""
        self.stop()
        total_ms = self.end_ms
        u = self.usage
        prompt = u.get("prompt_tokens")
        completion = u.get("completion_tokens")
        # 생각 토큰 수: usage 에 있으면 그 값, 없으면 total 에서 빠진 몫(Gemini), 그것도 없으면 생각 델타 개수(근사)
        hidden = u.get("total_tokens", 0) - (prompt or 0) - (completion or 0) if u else 0
        reasoning = (
                (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
                or (hidden if hidden > 0 else None)
                or (self.reasoning_chunks or None)
        )
        gen_ms = total_ms - self.first_ms if self.first_ms is not None else 0
        # 델타가 1개뿐이면(Gemini 가 답을 한 조각으로 보낸 경우) 생성 구간을 잴 수 없어 tps 를 비워 둔다
        measurable = self.reasoning_chunks + self.content_chunks >= 2
        tps = round(completion / (gen_ms / 1000), 1) if completion and gen_ms > 0 and measurable else None
        # 출력 과금 토큰 = total - prompt (숨은 생각 토큰도 출력으로 과금된다)
        cost = cost_usd(self.spec, prompt, u.get("total_tokens", prompt + (completion or 0)) - prompt) if prompt else None
        return {
            "prompt_tokens": prompt, "completion_tokens": completion, "reasoning_tokens": reasoning,
            "ttft_ms": self.first_ms, "ttft_content_ms": self.first_content_ms, "total_ms": total_ms,
            "tps": tps, "cost_usd": cost, "finish_reason": self.finish_reason,
        }

    def to_row(self) -> dict:
        return {**self.row, **self.summary(), "status": self.status, "error": self.error}