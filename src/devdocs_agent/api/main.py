"""FastAPI 앱. 실행: uv run uvicorn devdocs_agent.api.main:app --port 8000"""

from dotenv import load_dotenv
from fastapi import FastAPI

from devdocs_agent.api import chat
from devdocs_agent.llm.profiles import ROOT, load

load_dotenv(ROOT / ".env")  # 설정값은 요청 때 os.getenv 로 읽으므로 import 뒤에 불러도 된다
load()  # 프로필 검사(allow_egress)를 시작할 때 한 번 돌려, 잘못된 설정이면 서버가 뜨지 않게 한다

app = FastAPI(title="devdocs-agent")
app.include_router(chat.router)