"""X-API-Key 헤더 하나로 막는다. .env 의 API_KEY 가 비어 있으면 모든 요청을 거부한다."""

import os
import secrets

from fastapi import Header, HTTPException


async def require_key(x_api_key: str = Header(default="")) -> None:
    expected = os.getenv("API_KEY", "")
    if not expected or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "X-API-Key 가 없거나 맞지 않다")