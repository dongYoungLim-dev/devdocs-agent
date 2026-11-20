"""config/profiles.yaml 과 config/models.yaml 을 읽어 요청 한 번에 쓸 대상을 정한다."""

import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[3]
LOCAL_HOSTS = {"localhost", "127.0.0.1"}


@dataclass(frozen=True)
class Target:
    profile: str
    model: str
    base_url: str
    api_key: str = field(repr=False)  # print·로그에 키가 찍히지 않게 repr 에서 뺀다
    spec: dict  # models.yaml 의 한 행. 모델별 차이는 여기서만 읽는다


@cache
def load() -> tuple[dict, dict]:
    profiles = yaml.safe_load((ROOT / "config/profiles.yaml").read_text(encoding="utf-8"))["profiles"]
    models = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))["models"]
    for name, p in profiles.items():
        # 외부 통신을 막은 프로필이 외부 주소를 가리키면 시작부터 거부한다(설계서 2-4, 8장)
        if not p.get("allow_egress") and urlparse(p["llm"]["base_url"]).hostname not in LOCAL_HOSTS:
            raise ValueError(f"{name}: allow_egress=false 인데 llm.base_url 이 외부 주소다")
    return profiles, models


def resolve(profile: str | None = None, model: str | None = None) -> Target:
    """요청의 profile·model(없으면 .env 의 PROFILE 과 프로필 기본 모델)로 대상을 만든다."""
    profiles, models = load()
    name = profile or os.environ.get("PROFILE", "onprem")
    if name not in profiles:
        raise LookupError(f"profiles.yaml 에 없는 프로필: {name}")
    llm = profiles[name]["llm"]
    model = model or llm["model"]
    if model not in models:
        raise LookupError(f"models.yaml 에 없는 모델: {model}")
    if "api_key_env" in llm:  # 진짜 키는 .env 에만 있다(클라우드 프로필)
        key = os.getenv(llm["api_key_env"], "")
        if not key:
            raise LookupError(f"{name}: .env 의 {llm['api_key_env']} 가 비어 있다")
    else:  # 자리표시 값(Ollama 는 키를 검사하지 않는다)
        key = llm["api_key"]
    return Target(name, model, llm["base_url"], key, models[model])