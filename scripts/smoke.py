"""M0 smoke 시험: 모델마다 스트리밍 1회, 도구 호출 1회를 보내고 결과를 표로 출력한다.

실행: uv run python scripts/smoke.py [모델 ...]   (모델을 주면 그 모델만 시험한다)
원본: $PRIVATE_DIR/results/smoke-YYYYMMDD.jsonl (저장소 밖, 커밋하지 않는다)
"""


import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import APIStatusError, OpenAI, OpenAIError

ROOT = Path(__file__).resolve().parent.parent

# 시험 대상 접속 정보는 profiles.yaml 에서, 모델 정보는 models.yaml 에서만 읽는다.
TARGETS = [
    ("onprem", "qwen3.6:35b"),
    ("onprem", "gemma4:26b"),
    ("onprem", "gpt-oss:20b"),
    ("gemini", "gemini-4-hydrogen"),
    ("saas", "claude-haiku-5-5")
]

STREAM_MESSAGES = [
    {"role": "user", "content": "데이터베이스 인덱스가 무엇인지 한 문장으로 설명해 주세요."}
]

TOOL_MESSAGES = [
    {"role": "user", "content": "demo_inventory 테이블을 쓰는 SQL 쿼리를 찾아 주세요."}
]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_table",
            "description": "테이블 이름으로, 그 테이블을 읽거나 쓰는 SQL 문장을 찾는다.",
            "parameters": {
                "type": "object",
                "properties": {"table": {"type": "string", "description": "찾을 테이블 이름"}},
                "required": ["table"],
            }
        }
    }
]

EXPECTED_TOOL = ("find_table", {"table": "demo_inventory"})

# 델타에 오지만 생각 토큰이 아닌 필드. extra_content 는 Gemini 의 thought_signature(암호화된 서명)다
NOT_REASONING = ("content", "tool_calls", "extra_content")


def load_config() -> tuple[dict, dict]:
    load_dotenv(ROOT / ".env")
    profiles = yaml.safe_load((ROOT / "config/profiles.yaml").read_text(encoding="utf-8"))
    models = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))
    return profiles["profiles"], models["models"]

def make_client(llm: dict) -> OpenAI | None:
    """프로필의 llm 설정을 가지고 클라이언트를 만든다. 키는 .env 에서 가져 오고 없으면 None"""
    if "api_key_env" in llm:
        key = os.getenv(llm["api_key_env"], "")
        if not key:
            return None
    else:
        key = llm["api_key"]
    return OpenAI(base_url=llm["base_url"], api_key=key, timeout=300, max_retries=0)

def error_info(e: OpenAIError) -> dict:
    """예외를 jsonl 에 남길 dict 로 바꾼다. HTTP 오류면 상태 코드와 응답 본문도 남긴다.
    SDK 메시지에는 본문의 error.message 만 붙고, 본문이 그 모양이 아니면 상태 코드만 남는다."""
    info = {"error": f"{type(e).__name__}: {e}"}
    if isinstance(e, APIStatusError):
        info["status"] = e.status_code
        # e.body 는 JSON 이면 파싱한 값, 아니면 원문 텍스트다. 비어 있으면 응답 원문을 직접 읽는다
        info["body"] = e.body if e.body not in (None, "") else e.response.text[:2000]
    return info


class Pacer:
    """models.yaml 의 rpm을 넘지 않게 요청 사이를 띄운다. rpm 이 없으면 기다리지 않는다."""
    def __init__(self, rpm: int | None):
        self.gap = 60 / rpm if rpm else 0
        self.last = 0.0

    def wait(self) -> None:
        delay = self.last + self.gap - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def elapsed_ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def run_stream(client: OpenAI, params: dict, messages: list, tools: list | None = None) -> dict:
    """스트리밍 요청 1회. 첫 델타 시각, 델타에 나온 필드, usage, 도구 호출 조각을 모은다."""
    body = {**params, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
    if tools:
        body["tools"] = tools
    res = {
        "request": body, "ttft_ms": None, "ttft_content_ms": None, "fields": set(),
        "content": "", "tool_parts": {}, "usage": None, "chunks": [],
    }
    t0 = time.perf_counter()
    for chunk in client.chat.completions.create(**body):
        raw = chunk.model_dump(exclude_none=True)  # SDK 가 모르는 필드(reasoning 등)도 들어 있다
        res["chunks"].append(raw)
        if raw.get("usage"):
            res["usage"] = raw["usage"]
        if not raw.get("choices"):
            continue  # include_usage 의 마지막 청크는 choices 가 비어 있다
        delta = raw["choices"][0].get("delta", {})
        filled = {k for k, v in delta.items() if k != "role" and v not in ("", [], {})}
        if not filled:
            continue
        res["fields"] |= filled
        if res["ttft_ms"] is None:
            res["ttft_ms"] = elapsed_ms(t0)
        if delta.get("content"):
            res["content"] += delta["content"]
            if res["ttft_content_ms"] is None:
                res["ttft_content_ms"] = elapsed_ms(t0)
        # 도구 호출은 여러 청크로 쪼개져 온다. index 별로 이름과 인자 문자열을 이어 붙인다
        for tc in delta.get("tool_calls", []):
            part = res["tool_parts"].setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
            fn = tc.get("function", {})
            part["name"] += fn.get("name") or ""
            part["arguments"] += fn.get("arguments") or ""
    res["total_ms"] = elapsed_ms(t0)
    res["fields"] = sorted(res["fields"])
    return res

def check_tool(res: dict) -> tuple[str, str]:
    """첫 번째 도구 호출의 이름과 인자 파싱 결과를 돌려준다."""
    if not res["tool_parts"]:
        return "호출 안 함", "-"
    part = res["tool_parts"][min(res["tool_parts"])]
    try:
        args = json.loads(part["arguments"] or "{}")
    except json.JSONDecodeError:
        return part["name"], "파싱 실패"
    if (part["name"], args) == EXPECTED_TOOL:
        return part["name"], "일치"
    return part["name"], f"다름 {args}"



def run_model(client: OpenAI, model: str, spec: dict, row: dict) -> None:
    params = {"model": model}
    if spec.get("temperature") is not None:  # null 이면 요청에 넣지 않는다(Claude)
        params["temperature"] = spec["temperature"]
    pacer = Pacer(spec.get("rpm"))

    # 워밍업: 첫 요청에는 모델 로드 시간이 섞이므로 응답은 버리고 걸린 시간만 남긴다
    pacer.wait()
    t0 = time.perf_counter()
    try:
        client.chat.completions.create(**params, messages=[{"role": "user", "content": "ping"}])
        row["warmup_ms"] = elapsed_ms(t0)
    except OpenAIError as e:
        row["warmup_error"] = error_info(e)

    for name, messages, tools in (("stream", STREAM_MESSAGES, None), ("tool", TOOL_MESSAGES, TOOLS)):
        pacer.wait()
        try:
            row[name] = run_stream(client, params, messages, tools)
        except OpenAIError as e:  # 실패도 결과다. 유형과 메시지를 남기고 다음 시험으로 넘어간다
            row[name] = error_info(e)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=False, cwd=ROOT, capture_output=True, text=True).stdout.strip()

def app_commit() -> str:
    """측정 당시 코드 버전. 커밋 안 된 변경이 있으면 -dirty 를 붙인다."""
    return git("rev-parse", "--short", "HEAD") + ("-dirty" if git("status", "--porcelain") else "")

def ollama_version(base_url: str) -> str | None:
    """Ollama 고유 API(/api/version)로 서버 버전을 읽는다. OpenAI 호환 주소 끝의 /v1 을 떼고 부른다."""
    url = base_url.rstrip("/").removesuffix("/v1") + "/api/version"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)["version"]
    except OSError:
        return None


def summarize(row: dict) -> list[str]:
    """jsonl 한 줄을 표의 한 행으로 줄인다."""
    if "note" in row:
        return [row["model"], *["-"] * 7, row["note"]]
    s, t = row["stream"], row["tool"]
    notes = [f"워밍업 {row['warmup_ms']}ms" if "warmup_ms" in row else "워밍업 실패"]
    if "error" in s:
        stream_cols = ["실패", "-", "-", "-", "-"]
        notes.append(s["error"][:80])
    else:
        reasoning = [f for f in s["fields"] if f not in NOT_REASONING]
        if "extra_content" in s["fields"]:
            notes.append("생각 서명만 옴(extra_content)")
        stream_cols = [
            "성공" if s["content"] else "답 없음",
            str(s["ttft_ms"]),
            str(s["ttft_content_ms"]),
            ", ".join(reasoning) or "없음",
            "있음" if s["usage"] else "usage 없음",
            ]
    if "error" in t:
        tool_cols = ["실패", "-"]
        notes.append(t["error"][:80])
    else:
        tool_cols = list(check_tool(t))
    return [row["model"], *stream_cols, *tool_cols, "; ".join(notes)]


def print_table(rows: list[dict]) -> None:
    head = ["모델", "스트리밍", "ttft_ms", "ttft_content_ms", "생각 토큰 필드", "usage", "도구 호출", "인자 파싱", "비고"]
    print("| " + " | ".join(head) + " |")
    print("|" + "---|" * len(head))
    for row in rows:
        print("| " + " | ".join(c.replace("|", "/") for c in summarize(row)) + " |")

def main(only: list[str]) -> int:
    profiles, models = load_config()
    private_dir = os.getenv("PRIVATE_DIR")
    if not private_dir:
        print("PRIVATE_DIR 가 .env 에 없다. 결과를 저장소 안에 쓰지 않도록 여기서 멈춘다.", file=sys.stderr)
        return 1
    out_path = Path(private_dir) / "results" / f"smoke-{datetime.now():%Y%m%d}.jsonl"
    env = {"app_commit": app_commit(), "ollama_version": ollama_version(profiles["onprem"]["llm"]["base_url"])}

    rows = []
    with out_path.open("a", encoding="utf-8") as f:  # 같은 날 다시 돌리면 뒤에 덧붙는다
        for profile, model in TARGETS:
            if only and model not in only:
                continue
            llm, spec = profiles[profile]["llm"], models.get(model)
            row = {"at": datetime.now().isoformat(timespec="seconds"), "profile": profile, "model": model, **env}
            client = make_client(llm)
            if spec is None:
                row["note"] = "models.yaml 에 없음"
            elif client is None:
                row["note"] = f"보류: .env 의 {llm['api_key_env']} 가 비어 있음"
            else:
                print(f"{model} 시험 중...", file=sys.stderr)
                run_model(client, model, spec, row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)

    print_table(rows)
    print(f"\n원본: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))