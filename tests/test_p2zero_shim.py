# SPDX-License-Identifier: Apache-2.0
# TCSE fork addition; not part of upstream RRSI.
"""Offline gate for P2-zero: the Claude Code adapter and the coding_mini domain.

No model, no Docker, no network beyond localhost: a fake `claude` executable
records exactly what the adapter would hand to Claude Code, and RRSI's own
provider-flex LiteLLM backend is pointed at the adapter.

    python -m pytest -q tests/test_p2zero_shim.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "tcse_claude_cli_shim", ROOT / "scripts" / "tcse_claude_cli_shim.py")
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)

FAKE_TOKEN = "fake-oauth-token-for-the-offline-gate"
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
cfg = os.environ.get("CLAUDE_CONFIG_DIR")
rec = {"argv": args, "stdin": sys.stdin.read(), "cwd_entries": sorted(os.listdir(".")),
       "config_dir_entries": sorted(os.listdir(cfg)) if cfg else None,
       "env_has_api_key": "ANTHROPIC_API_KEY" in os.environ,
       "env_has_auth_token": "ANTHROPIC_AUTH_TOKEN" in os.environ,
       "oauth_token_present": bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))}
rec["system"] = Path(args[args.index("--system-prompt-file") + 1]).read_text()
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "limit":
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True,
                      "result": "Claude usage limit reached. Your limit will reset at 5pm."}))
    sys.exit(1)
if mode == "fail":
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True,
                      "result": "transient server error " + os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")}))
    sys.exit(1)
reply = os.environ.get("FAKE_CLAUDE_REPLY", '{"verdict": "accept", "reasons": [], "risk_notes": []}')
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply,
                  "num_turns": 1, "total_cost_usd": 0.001,
                  "usage": {"input_tokens": 70, "output_tokens": 30,
                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                  "modelUsage": {"claude-opus-5-5": {}}}))
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "fake_claude_calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-be-stripped")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-be-stripped")
    monkeypatch.delenv("FAKE_CLAUDE_MODE", raising=False)
    return exe, log


def _start(adapter):
    server = shim.make_server(adapter, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def _post(base, body):
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _calls(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _msgs(system):
    return [{"role": "system", "content": system}, {"role": "user", "content": "x"}]


def test_coding_mini_is_the_unmodified_coding_adapter(monkeypatch):
    for v in ("RRSI_SEARCH_MODEL", "RRSI_PROPOSER_MODEL", "RRSI_ANALYST_MODEL",
              "RRSI_CRITIC_MODEL", "RRSI_POLICY_MODEL"):
        monkeypatch.delenv(v, raising=False)
    from rrsi.config import RRSIConfig
    from rrsi.domain import load_domain
    d = load_domain("coding_mini")
    coding = sys.modules["domains.coding.adapter"]
    assert type(d).__mro__[1] is coding.CodingDomain
    assert d.name == "coding_mini"
    assert d.evolve_ids() == ["extract-elf", "fix-git"] == d.smoke_ids()
    assert d.harness_dir(ROOT) == (ROOT / "third_party" / "harbor_terminus2").resolve()
    assert os.path.normpath(f"domains/{d.name}/{d.harness_path}") == \
        os.path.join("third_party", "harbor_terminus2")
    skill, patterns = d.constitution(ROOT)
    assert skill == (ROOT / "domains" / "coding" / "SKILL.md").read_text()
    assert patterns == (ROOT / "domains" / "coding" / "PATTERNS.md").read_text()
    assert len(d.critic_patterns) == 7 + 89            # verifier denylist + every task name
    assert set(d.briefs) == {"analyst", "digester", "proposer", "critic"}
    cfg = RRSIConfig.load(d.root / "rrsi.json")
    assert (cfg.T, cfg.k, cfg.m, cfg.repair_rounds) == (1, 1, 1, 2)
    base = json.loads((ROOT / "domains" / "coding" / "rrsi.json").read_text())
    mini = json.loads((d.root / "rrsi.json").read_text())
    for key in ("b_min", "b_max", "w", "m_draft", "delta", "delta_z", "beta0", "beta1",
                "w_s", "w_c", "w_n", "n_prune", "invalid_missing_frac", "n_fail_traces",
                "n_success_traces", "eval_parallel"):
        assert mini[key] == base[key], key                # method hyperparameters unchanged


def test_rrsi_generate_reaches_claude_code_through_the_adapter(fake_claude, tmp_path, monkeypatch):
    exe, log = fake_claude
    ledger, dump = tmp_path / "ledger.jsonl", tmp_path / "dump"
    adapter = shim.Adapter(claude_bin=str(exe), ledger=ledger, dump_dir=dump)
    server, base = _start(adapter)
    try:
        monkeypatch.setenv("RRSI_SEARCH_BACKEND", "litellm")
        monkeypatch.setenv("RRSI_SEARCH_API_BASE", base)
        monkeypatch.setenv("RRSI_SEARCH_API_KEY", "tcse-local-adapter")
        monkeypatch.setenv("RRSI_SEARCH_MAX_TOKENS", "20000")
        monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        from rrsi import llm
        out = llm.generate("PROMPT-BODY", system="You are a strict reviewer of harness code.",
                           json_only=True, model="openai/opus", cache_prefix="CACHE-PREFIX",
                           max_retries=1)
    finally:
        server.shutdown()
        server.server_close()
    assert json.loads(out) == {"verdict": "accept", "reasons": [], "risk_notes": []}
    calls = _calls(log)
    assert len(calls) == 1
    c, a = calls[0], calls[0]["argv"]
    assert a[0] == "-p" and a[a.index("--output-format") + 1] == "json"
    assert a[a.index("--model") + 1] == "opus"
    assert a[a.index("--tools") + 1] == ""
    assert a[a.index("--max-turns") + 1] == "1"
    assert a[a.index("--disallowedTools") + 1] == "mcp__*"
    assert "--strict-mcp-config" in a and "--no-session-persistence" in a
    assert c["system"] == "You are a strict reviewer of harness code." + llm._JSON_SUFFIX
    assert c["stdin"] == "CACHE-PREFIX\n\nPROMPT-BODY"      # RRSI's exact user text, nothing added
    assert c["cwd_entries"] == [] and c["config_dir_entries"] == []
    assert not c["env_has_api_key"] and not c["env_has_auth_token"]
    assert c["oauth_token_present"]
    rec = _calls(ledger)
    assert len(rec) == 1 and rec[0]["role"] == "critic" and rec[0]["status"] == 200
    assert rec[0]["served_models"] == ["claude-opus-5-5"] and rec[0]["tokens"] == 100
    assert sorted(p.name for p in dump.iterdir()) == [
        "0001_critic_prompt.txt", "0001_critic_stderr.txt", "0001_critic_stdout.json",
        "0001_critic_system.txt"]


def test_zero_spend_guards_refuse_and_cap(fake_claude, tmp_path):
    exe, log = fake_claude
    adapter = shim.Adapter(claude_bin=str(exe), ledger=tmp_path / "ledger.jsonl", token_cap=150)
    server, base = _start(adapter)
    try:
        msgs = _msgs("You are a harness engineer agent.")
        st, body = _post(base, {"model": "claude-fable-5-2", "messages": msgs})
        assert st == 400 and "refused" in body["error"]["message"]
        st, _ = _post(base, {"model": "opus", "messages": msgs})       # 100 tokens
        assert st == 200 and adapter.halted is None
        st, _ = _post(base, {"model": "opus", "messages": msgs})       # 200 >= cap: halts after
        assert st == 200 and adapter.halted and "token cap" in adapter.halted
        st, body = _post(base, {"model": "opus", "messages": msgs})
        assert st == 429 and "halted" in body["error"]["message"]
    finally:
        server.shutdown()
        server.server_close()
    assert len(_calls(log)) == 2                     # refused and halted calls never reach claude
    roles = {r["role"] for r in _calls(tmp_path / "ledger.jsonl")}
    assert roles == {"proposer"}


def test_usage_limit_halts_and_errors_do_not(fake_claude, tmp_path, monkeypatch):
    exe, log = fake_claude
    ledger = tmp_path / "ledger.jsonl"
    adapter = shim.Adapter(claude_bin=str(exe), ledger=ledger, dump_dir=tmp_path / "dump")
    server, base = _start(adapter)
    try:
        msgs = _msgs("You are the batch analyst in a harness-evolution loop.")
        monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
        st, body = _post(base, {"model": "opus", "messages": msgs})
        assert st == 502 and adapter.halted is None               # a plain failure is not a halt
        monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
        st, body = _post(base, {"model": "opus", "messages": msgs})
        assert st == 429 and "limit" in body["error"]["message"].lower()
        assert adapter.halted and "usage limit" in adapter.halted
        monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
        st, body = _post(base, {"model": "opus", "messages": msgs})
        assert st == 429 and "halted" in body["error"]["message"]
    finally:
        server.shutdown()
        server.server_close()
    assert len(_calls(log)) == 2                     # no claude run after the halt
    written = ledger.read_text() + "".join(p.read_text() for p in (tmp_path / "dump").iterdir())
    assert FAKE_TOKEN not in written                 # the token is scrubbed from every file


def test_adapter_refuses_non_loopback_bind():
    with pytest.raises(SystemExit):
        shim.main(["--host", "0.0.0.0", "--port", "0"])
