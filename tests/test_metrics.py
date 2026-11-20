"""토큰·비용 계산 (설계서 8장 단위 테스트)."""

import time
import uuid

from devdocs_agent.llm.metrics import Meter
from devdocs_agent.llm.pricing import cost_usd

SPEC = {"price_in": 0.10, "price_out": 0.50}


def test_cost():
    assert cost_usd(SPEC, 1_000_000, 1_000_000) == 0.6
    assert cost_usd({}, 1000, 1000) == 0


def test_hidden_reasoning_tokens_from_total():
    """Gemini 처럼 생각 토큰이 total 에만 들어 있는 경우(M0: 536 - 44 - 52 = 440)."""
    m = Meter(uuid.uuid4(), "gemini", "g", SPEC, "rag")
    m.usage = {"prompt_tokens": 44, "completion_tokens": 52, "total_tokens": 536}
    s = m.summary()
    assert s["reasoning_tokens"] == 440
    assert s["cost_usd"] == cost_usd(SPEC, 44, 492)  # 출력 과금 = total - prompt


def test_reasoning_tokens_from_chunks_when_usage_has_none():
    """Ollama 는 usage 에 생각 토큰 수가 없어 생각 델타 개수로 근사한다."""
    m = Meter(uuid.uuid4(), "onprem", "q", {}, "rag")
    for _ in range(5):
        m.mark_reasoning()
    m.mark_content()
    m.usage = {"prompt_tokens": 25, "completion_tokens": 30, "total_tokens": 55}
    s = m.summary()
    assert s["reasoning_tokens"] == 5
    assert s["ttft_ms"] <= s["ttft_content_ms"]


def test_tps_is_none_for_single_chunk():
    """답이 한 조각으로 오면(11-20 Gemini: 12토큰이 19ms 만에 한 번에 와서 631.6) tps 를 비워 둔다."""
    m = Meter(uuid.uuid4(), "gemini", "g", {}, "rag")
    m.mark_content()
    time.sleep(0.02)
    m.usage = {"prompt_tokens": 32, "completion_tokens": 12, "total_tokens": 75}
    assert m.summary()["tps"] is None


def test_tps_is_measured_for_streamed_chunks():
    m = Meter(uuid.uuid4(), "onprem", "q", {}, "rag")
    m.mark_reasoning()
    time.sleep(0.02)
    m.mark_content()
    m.usage = {"prompt_tokens": 25, "completion_tokens": 30, "total_tokens": 55}
    assert m.summary()["tps"] > 0


def test_summary_is_stable():
    """usage 이벤트와 requests 행이 같은 total_ms 를 쓴다(summary 를 두 번 불러도 같다)."""
    m = Meter(uuid.uuid4(), "onprem", "q", {}, "rag")
    first = m.summary()["total_ms"]
    time.sleep(0.02)
    assert m.to_row()["total_ms"] == first


def test_status_defaults_to_cancelled():
    assert Meter(uuid.uuid4(), "onprem", "q", {}, "rag").to_row()["status"] == "cancelled"