# devdocs-agent

직접 만든 디자인 시스템, 프론트·백엔드 개발 하네스, 앱의 SQL 매퍼, 개발 원칙 문서에 대해 질문하면 출처를 붙여 답하는 개발 지식 에이전트입니다.

> 구현 중입니다(2026-11-19 ~ 12-02). 이 저장소에는 코드만 올리고, 코퍼스와 평가셋은 공개하지 않습니다.

## 하는 일

- **두 계층 검색**
  - 구조 계층: 매퍼 XML 을 파싱한 SQL 인덱스, 컴포넌트 API, 디자인 토큰. "이 테이블을 쓰는 쿼리"처럼 관계를 묻는 질문에 확정 답을 줍니다.
  - 본문 계층: BM25 + bge-m3 dense(pgvector) + RRF + bge-reranker-v2-m3. "왜 이렇게 정했나"처럼 문서를 읽어야 하는 질문을 맡습니다.
- **사본 접기**: 여러 저장소에 복사된 같은 문서를 내용 해시로 대표 문서 하나에 묶습니다.
- **LLM 직접 호출**: OpenAI 호환 API 로 로컬 모델(Ollama)과 클라우드 모델을 설정만 바꿔 교체합니다.
- **스트리밍 API**: FastAPI SSE 로 검색 결과, 토큰, 도구 호출, 사용량을 차례로 보냅니다. 첫 토큰 시간(TTFT)을 잽니다.
- **도구와 승인**: MCP 서버로 검색·조회 도구를 제공하고, 이슈 생성 같은 위험 도구는 사람 승인 뒤에만 실행합니다.
- **평가**: 검색 정확도, 답변 정답률, 도구 호출 성공률, 초당 토큰, TTFT, 요청당 비용을 모델별로 비교합니다.

## 기술

Python 3.13 · FastAPI · PostgreSQL + pgvector · Ollama · MCP · Langfuse · Docker

## 실행

지금은 M1 단계입니다. 검색 없이 LLM 답변을 SSE 로 흘리고, 요청마다 지표를 `requests` 테이블에 남깁니다.

```bash
cp .env.example .env    # API_KEY 를 채운다. 비어 있으면 모든 /v1/chat 요청이 401 이다
docker compose up -d
docker compose exec -T devdocs-pg psql -U devdocs -d devdocs -v ON_ERROR_STOP=1 < src/devdocs_agent/db/schema.sql
uv run uvicorn devdocs_agent.api.main:app --port 8000
```

```bash
curl -N -X POST localhost:8000/v1/chat -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"message":"인덱스를 한 문장으로 설명해 주세요.","model":"gpt-oss:20b"}'
# event: meta → event: token … → event: usage → event: done
```

테스트는 가짜 LLM 으로 돌아서 Ollama 와 DB 가 없어도 됩니다.

```bash
uv run pytest -m "not live"
```

`scripts/smoke.py` 는 M0 에서 모델마다 스트리밍과 도구 호출이 되는지 점검한 도구입니다. 그래서 "LLM 호출은 `llm/client.py` 에서만" 규칙의 예외로, OpenAI SDK 를 직접 부릅니다.
