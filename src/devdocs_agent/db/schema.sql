-- M1 에서는 requests 만 만든다. 나머지 테이블은 쓰는 마일스톤에서 추가한다(설계서 2-8)
create extension if not exists vector;

create table if not exists requests (
    id                uuid primary key,          -- SSE 이벤트의 request_id 와 같은 값
    created_at        timestamptz default now(),
    profile           text,
    model             text,
    mode              text,
    step              int,                       -- 에이전트 루프 단계(M4). M1 은 1
    prompt_tokens     int,
    completion_tokens int,
    reasoning_tokens  int,                       -- 추정값일 수 있다(metrics.py 참고)
    ttft_ms           int,                       -- 첫 델타(생각 포함)
    ttft_content_ms   int,                       -- 첫 답변 토큰
    total_ms          int,
    tps               real,                      -- 생성 구간 초당 출력 토큰
    cost_usd          numeric(10,6),
    finish_reason     text,                      -- stop | length | tool_calls. length 면 max_tokens 에 잘린 것
    status            text check (status in ('ok', 'error', 'cancelled')),
    error             text                       -- 실패 시 {"type", "status", "body"} JSON
);