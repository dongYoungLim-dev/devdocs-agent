"""allow_egress=false 프로필이 외부 주소를 가리키면 거부 (설계서 8장)."""

import pytest
import yaml

from devdocs_agent.llm import profiles


def test_egress_false_with_external_url_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    bad = {"profiles": {"onprem": {"llm": {"base_url": "https://api.example.com/v1", "api_key": "x", "model": "m"},
                                   "allow_egress": False}}}
    (tmp_path / "config/profiles.yaml").write_text(yaml.safe_dump(bad))
    (tmp_path / "config/models.yaml").write_text(yaml.safe_dump({"models": {"m": {}}}))
    monkeypatch.setattr(profiles, "ROOT", tmp_path)
    profiles.load.cache_clear()
    try:
        with pytest.raises(ValueError, match="allow_egress"):
            profiles.load()
    finally:
        profiles.load.cache_clear()