"""DB 쓰기. M1 은 requests 1행 쓰기만 있다."""

import logging
import os

import psycopg

log = logging.getLogger(__name__)

REQUEST_COLUMNS = (
    "id", "profile", "model", "mode", "step", "prompt_tokens", "completion_tokens", "reasoning_tokens",
    "ttft_ms", "ttft_content_ms", "total_ms", "tps", "cost_usd", "finish_reason", "status", "error",
)


async def insert_request(row: dict) -> None:
    """requests 에 1행을 쓴다. DB 가 죽어도 답변 스트림은 끊지 않고 경고 로그만 남긴다."""
    sql = (
        f"insert into requests ({', '.join(REQUEST_COLUMNS)}) "
        f"values ({', '.join(['%s'] * len(REQUEST_COLUMNS))})"
    )
    try:
        # with 블록이 예외 없이 끝나면 커밋된다
        async with await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"]) as conn:
            await conn.execute(sql, [row.get(c) for c in REQUEST_COLUMNS])
    except (psycopg.Error, KeyError) as e:
        log.warning("requests 기록 실패 id=%s: %s", row.get("id"), e)