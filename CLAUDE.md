# devdocs-agent 작업 규칙

## 데이터
- 코퍼스 내용(문서 본문, 코드, SQL)을 이 저장소에 커밋하지 않는다. 평가셋·결과·코퍼스 설정은 `PRIVATE_DIR` 에만 둔다.
- 원본 저장소는 읽기 전용이다. 적재 코드가 원본에 쓰면 안 된다.
- 허용 목록(`corpus.yaml`) 밖 경로를 읽지 않는다. 운영 데이터가 섞일 수 있는 경로(`ops/`, `uploads/`, `.env*`, 백업 덤프)는 허용 목록에 있어도 읽지 않는다.
- 테스트·예시의 사람 이름은 가상 이름만 쓴다. 커밋 전에 실명을 grep 한다.
- Langfuse 로 청크 본문을 보내지 않는다(ID·경로만).

## 코드
- 모델 이름으로 분기하지 않는다. 모델별 차이는 `config/models.yaml` 에만 둔다.
- LLM 호출은 `llm/client.py` 한 곳에서만 한다. 모든 호출은 `requests` 테이블에 1행을 남긴다.
- 위험 도구 승인 판단은 호스트(`agent/approvals.py`)와 MCP 서버 양쪽에 있다. 한쪽만 고치지 않는다.

## 평가·측정
- 평가셋은 버전 파일이다. 기존 파일을 고치지 말고 새 버전 파일을 만든다.
- 측정 수치를 README 에 쓸 때 ollama 버전, 모델 digest, 앱 커밋, 원본별 커밋을 같이 적는다.

## 커밋 전
- `uv run pytest -m "not live"`, `ruff check`, `gitleaks detect` 를 통과시킨다.
