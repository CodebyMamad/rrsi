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
       "token_received": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")}
rec["system"] = Path(args[args.index("--system-prompt-file") + 1]).read_text()
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps(rec) + "\n")
# Field set copied from a real Claude Code 2.1.311 result (run 43794753499):
# every result, success or failure, carries "fallback_credit" in usage.
USAGE = {"input_tokens": 70, "output_tokens": 30, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 0, "service_tier": "standard", "speed": "standard",
         "fallback_credit": None}
FAIL = {"overloaded": (529, "API Error: 529 Overloaded"),
        "auth": (401, "Failed to authenticate. API Error: 401 Invalid bearer token"),
        "limit": (429, "Claude usage limit reached. Your limit will reset at 5pm."),
        "echo": (500, "server error near " + os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""))}
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode in FAIL:
    status, message = FAIL[mode]
    print(json.dumps({"type": "result", "subtype": "success", "is_error": True,
                      "result": message, "api_error_status": status,
                      "api_error_origin": "first_party", "total_cost_usd": 0,
                      "usage": {**USAGE, "input_tokens": 0, "output_tokens": 0},
                      "modelUsage": {}}))
    sys.exit(1)
reply = os.environ.get("FAKE_CLAUDE_REPLY", '{"verdict": "accept", "reasons": [], "risk_notes": []}')
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply,
                  "num_turns": 1, "total_cost_usd": 0.001, "usage": USAGE,
                  "modelUsage": {"claude-opus-5-5": {}}}))
'''
# A long token copied from a wrapped terminal line: stray spaces and line breaks.
WRAPPED_TOKEN = "  " + FAKE_TOKEN[:12] + "\n" + FAKE_TOKEN[12:] + " \r\n"


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(FAKE_CLAUDE)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "fake_claude_calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", WRAPPED_TOKEN)
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
    assert c["token_received"] == FAKE_TOKEN          # wrapped copy repaired: whitespace removed
    assert adapter.token_diagnostics() == {"present": True, "length": len(FAKE_TOKEN),
                                           "whitespace_removed": True,
                                           "setup_token_prefix": False,
                                           "looks_like_api_key": False}
    rec = _calls(ledger)
    assert len(rec) == 1 and rec[0]["role"] == "critic" and rec[0]["status"] == 200
    assert rec[0]["served_models"] == ["claude-opus-5-5"] and rec[0]["tokens"] == 100
    assert sorted(p.name for p in dump.iterdir()) == [
        "0001_critic_prompt.txt", "0001_critic_stderr.txt", "0001_critic_stdout.json",
        "0001_critic_system.txt"]


def test_zero_spend_guards_refuse_and_cap(fake_claude, tmp_path):
    # 2026-11-16 semantics: the cap is enforced BEFORE a call by reserving its
    # upper bound (tests/test_shim_budget.py holds the full budget gate).
    exe, log = fake_claude
    system = "You are a harness engineer agent."
    reservation = shim.BudgetGuard().reservation(system, "x", shim.DEFAULT_MAX_OUTPUT_TOKENS)
    adapter = shim.Adapter(claude_bin=str(exe), ledger=tmp_path / "ledger.jsonl",
                           token_cap=reservation + 50)
    server, base = _start(adapter)
    try:
        msgs = _msgs(system)
        st, body = _post(base, {"model": "claude-fable-5-2", "messages": msgs})
        assert st == 400 and "refused" in body["error"]["message"]
        st, _ = _post(base, {"model": "opus", "messages": msgs})       # fits; uses 100 tokens
        assert st == 200 and adapter.halted is None
        st, body = _post(base, {"model": "opus", "messages": msgs})    # 100 + reservation > cap
        assert st == 429 and "halted" in body["error"]["message"]
        assert adapter.halted and "token budget" in adapter.halted
        st, body = _post(base, {"model": "opus", "messages": msgs})
        assert st == 429 and "halted" in body["error"]["message"]
    finally:
        server.shutdown()
        server.server_close()
    assert len(_calls(log)) == 1                     # refused and halted calls never reach claude
    rec = _calls(tmp_path / "ledger.jsonl")
    assert {r["role"] for r in rec} == {"proposer"}
    assert [r["failure_class"] for r in rec] == ["model_refused", None, "budget_refused",
                                                 "halted_refused"]
    assert adapter.guard.state()["charged"] == 100


def _sequence(exe, tmp_path, monkeypatch, modes):
    """Run one request per mode against a fresh adapter; return (adapter, results)."""
    adapter = shim.Adapter(claude_bin=str(exe), ledger=tmp_path / "ledger.jsonl",
                           dump_dir=tmp_path / "dump")
    server, base = _start(adapter)
    results = []
    try:
        for mode in modes:
            monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
            results.append(_post(base, {"model": "opus",
                                        "messages": _msgs("You are the batch analyst.")}))
    finally:
        server.shutdown()
        server.server_close()
    return adapter, results


def test_transient_failure_does_not_halt_despite_credit_field(fake_claude, tmp_path, monkeypatch):
    # Regression for run 43794753499: every Claude Code result carries the JSON
    # field "fallback_credit"; it must not be read as a usage-credit message.
    exe, log = fake_claude
    adapter, res = _sequence(exe, tmp_path, monkeypatch, ["overloaded", "ok"])
    assert res[0][0] == 502 and adapter.halted is None
    assert res[1][0] == 200 and len(_calls(log)) == 2
    rec = _calls(tmp_path / "ledger.jsonl")
    assert [r["failure_class"] for r in rec] == ["transient", None]
    assert rec[0]["api_error_status"] == 529


def test_authentication_failure_halts_with_401(fake_claude, tmp_path, monkeypatch):
    exe, log = fake_claude
    adapter, res = _sequence(exe, tmp_path, monkeypatch, ["auth", "ok"])
    assert res[0][0] == 401 and res[0][1]["error"]["type"] == "authentication_error"
    assert adapter.halted == "authentication failed (HTTP 401)"
    assert res[1][0] == 401 and "halted" in res[1][1]["error"]["message"]
    assert len(_calls(log)) == 1                     # no claude run after the halt
    assert _calls(tmp_path / "ledger.jsonl")[0]["failure_class"] == "auth"


def test_usage_limit_halts_with_429(fake_claude, tmp_path, monkeypatch):
    exe, log = fake_claude
    adapter, res = _sequence(exe, tmp_path, monkeypatch, ["limit", "ok"])
    assert res[0][0] == 429 and adapter.halted.startswith("usage limit")
    assert res[1][0] == 429 and "halted" in res[1][1]["error"]["message"]
    assert len(_calls(log)) == 1


def test_token_is_never_written_in_any_form(fake_claude, tmp_path, monkeypatch):
    exe, _ = fake_claude
    adapter, res = _sequence(exe, tmp_path, monkeypatch, ["echo"])
    assert res[0][0] == 502 and FAKE_TOKEN not in json.dumps(res[0][1])
    written = (tmp_path / "ledger.jsonl").read_text() + "".join(
        p.read_text() for p in (tmp_path / "dump").iterdir())
    assert "[redacted]" in written
    assert FAKE_TOKEN not in written and WRAPPED_TOKEN.strip() not in written


def test_api_key_shaped_token_is_refused_at_startup(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-api03-" + "x" * 24)
    with pytest.raises(SystemExit):
        shim.main(["--port", "0"])


def test_adapter_refuses_non_loopback_bind():
    with pytest.raises(SystemExit):
        shim.main(["--host", "0.0.0.0", "--port", "0"])
