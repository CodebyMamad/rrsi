"""Provider-flex regression tests: no network, no API keys, no Docker required."""
from __future__ import annotations
import json
import os
import sys
import types
from unittest.mock import patch

def test_default_search_uses_litellm(monkeypatch):
    from rrsi import llm
    seen = []
    fake = types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"action":"report"}'))])
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=lambda **kwargs: seen.append(kwargs) or fake))
    monkeypatch.setenv("RRSI_SEARCH_BACKEND", "litellm")
    monkeypatch.setenv("RRSI_SEARCH_API_BASE", "http://127.0.0.1:11434")
    assert llm.generate("hello", system="role", model="ollama_chat/test", json_only=True, cache_prefix="cache") == '{"action":"report"}'
    assert seen[0]["model"] == "ollama_chat/test"
    assert seen[0]["api_base"] == "http://127.0.0.1:11434"
    assert seen[0]["messages"][-1]["content"].startswith("cache")

def test_config_roles_can_be_overridden(tmp_path, monkeypatch):
    from rrsi.config import RRSIConfig
    path = tmp_path / "rrsi.json"
    path.write_text(json.dumps({"T": 3, "k": 1}))
    monkeypatch.setenv("RRSI_SEARCH_MODEL", "ollama_chat/test")
    monkeypatch.setenv("RRSI_CRITIC_MODEL", "openai/custom")
    cfg = RRSIConfig.load(path)
    assert cfg.proposer_model == "ollama_chat/test"
    assert cfg.analyst_model == "ollama_chat/test"
    assert cfg.critic_model == "openai/custom"

def test_vertex_not_required_on_import(monkeypatch):
    monkeypatch.delenv("VERTEXAI_PROJECT", raising=False)
    monkeypatch.delenv("RRSI_VERTEX_PROJECTS", raising=False)
    from rrsi import llm
    assert callable(llm.generate)
