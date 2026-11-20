"""SSE 한 덩어리 형식. 이벤트 순서는 chat.py 가 정한다."""

import json

PING = ": ping\n\n"  # 콜론으로 시작하는 줄은 SSE 주석이라 클라이언트가 무시한다. 연결 유지용


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"