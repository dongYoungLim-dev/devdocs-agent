"""가짜 LLM. client.stream_chat 과 같은 모양으로 정해 둔 이벤트를 낸다."""

import asyncio

from devdocs_agent.llm import client

USAGE = {"prompt_tokens": 10, "completion_tokens": 3, "reasoning_tokens": 1, "ttft_ms": 1, "ttft_content_ms": 2,
         "total_ms": 3, "tps": 1.0, "cost_usd": 0.0, "finish_reason": "stop"}


class FakeLLM:
    def __init__(self, events: list, delay: float = 0):
        self.events = events
        self.delay = delay
        self.closed = False  # 상류 스트림을 닫았는지(끊김 테스트에서 본다)

    async def __call__(self, target, messages, *, request_id, mode="rag"):
        try:
            for ev in self.events:
                await asyncio.sleep(self.delay)
                if isinstance(ev, Exception):
                    raise ev
                yield ev
        finally:
            self.closed = True


def normal() -> list:
    return [client.Reasoning("생각"), client.Token("안"), client.Token("녕"), client.Done(USAGE)]