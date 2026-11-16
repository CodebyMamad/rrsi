#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# TCSE fork addition; not part of upstream RRSI.
"""Localhost OpenAI-compatible adapter that answers RRSI's search-role calls
with Claude Code in print mode, authenticated by the repository owner's own
Claude subscription (CLAUDE_CODE_OAUTH_TOKEN, created with `claude setup-token`).

RRSI is not modified. Provider-flex's LiteLLM backend is pointed here with
    RRSI_SEARCH_MODEL=openai/<claude-code-model>
    RRSI_SEARCH_API_BASE=http://127.0.0.1:<port>/v1
and every chat-completion request becomes one isolated, tool-less,
single-turn `claude -p` run: RRSI's system text replaces Claude Code's system
prompt, RRSI's user text is piped on stdin, built-in tools and MCP are
disabled, and the run starts in an empty directory with an empty config
directory, so no CLAUDE.md, settings, memory or plugins enter the context.

Zero-spend guards (the owner's budget is $0):
  * API-key, bearer-token, profile and cloud-provider variables are removed
    from the child environment, so Claude Code cannot fall back to API billing;
  * Fable and Mythos models are refused when requested and halt the adapter
    when served (on some plans they bill usage credits);
  * failures are classified from Claude Code's own `api_error_status` and its
    result message, never from raw JSON (every result carries field names
    such as `fallback_credit`); see FAILURE CLASSES below;
  * a token budget and a call cap are enforced BEFORE each call; see BUDGET.
The account-side guard (usage credits switched off) is the owner's.

BUDGET (repaired 2026-11-16). At d155b43 the cap was compared only after a
call had finished, so `--token-cap 1` let a 401-token call through (run
43799511286); requests already queued were never re-checked; and timed-out or
unparseable calls were counted as 0 tokens. Now each call reserves an upper
bound on its usage before it may start,
    R = overhead_tokens + utf8_bytes(system + prompt) + output_cap,
and starts only while  charged + reserved_in_flight + R <= token_cap.
Admission, the start check and settlement share one lock. A settled call is
charged its reported usage, or R when its usage cannot be read (timeout,
crash, unparseable output). output_cap = min(request max_tokens,
--max-output-tokens) is passed to Claude Code as CLAUDE_CODE_MAX_OUTPUT_TOKENS.
  HARD, by construction and independent of the provider:
    G1  claude invocations <= --max-calls (when set);
    G2  no invocation starts unless the admission inequality holds;
    G3  no invocation starts after a halt (re-checked after the parallelism wait);
    G4  a call of unknown usage is charged its full reservation.
  CONDITIONAL: total usage <= token_cap holds only while every call stays
  within its reservation, which rests on assumptions the adapter cannot enforce:
    A1  at most one token per UTF-8 byte of RRSI's text (byte-level BPE; the
        Claude tokenizer is not public);
    A2  Claude Code adds at most --overhead-tokens of its own input (397 input
        tokens were measured for 76 bytes of text on 2.1.311, run 43799511286);
    A3  one model request per invocation (`--max-turns 1`, no tools);
    A4  Claude Code honours CLAUDE_CODE_MAX_OUTPUT_TOKENS as an output ceiling.
  Every settled call is checked against A1-A4. A call that used more than its
  reservation is recorded as an overrun and halts the adapter. Calls already
  running cannot be stopped, so the worst case is token_cap plus the overruns
  of at most --max-parallel calls. Neither Claude Code nor a subscription
  exposes an enforceable per-request input limit: this is conservative
  admission control, not a provider-enforced hard cap. A token check that
  must allow exactly one call uses --max-calls 1 and a budget that fits it.

FAILURE CLASSES (ledger `failure_class`, then the HTTP status sent to RRSI):
  before any invocation: halted_refused (the halt's 401 or 429),
    budget_refused and call_cap_refused (429, and the adapter halts),
    model_refused and empty_prompt (400);
  after an invocation: auth (401, halt), limit (429, halt),
    invalid_request (400: deterministic, so the OpenAI client does not retry),
    transient (502: API 5xx, 529 or 408), and timeout, launch_error,
    unparseable_output, cli_error, empty_result, other (502).

The token is read once at start-up with all whitespace removed (a long token
copied from a wrapped terminal line can carry line breaks); only non-secret
diagnostics about it are printed. Every call is recorded in a JSONL ledger;
prompts, system texts and raw Claude Code output can be dumped for audit.
Every form of the token is scrubbed from everything written to disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
             "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID",
             "ANTHROPIC_ORGANIZATION_ID", "CLAUDE_CODE_USE_BEDROCK",
             "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
REFUSED_MODEL = re.compile(r"fable|mythos", re.I)
# Matched against Claude Code's result message and stderr only, never raw JSON.
AUTH_TEXT = re.compile(r"failed to authenticate|invalid bearer|invalid api key|not logged in|"
                       r"please run /login|login expired|token (has )?expired|"
                       r"authentication_error|oauth_org_not_allowed", re.I)
LIMIT_TEXT = re.compile(r"usage limit|limit reached|hit your limit|limit will reset|"
                        r"rate.?limit|quota|out of (extra )?usage|usage credits?|"
                        r"credit balance|billing", re.I)
INVALID_TEXT = re.compile(r"prompt is too long|context (length|window)|too many (input )?tokens|"
                          r"maximum context|invalid_request_error", re.I)
AUTH_STATUS = (401, 403)
LIMIT_STATUS = (402, 429)
INVALID_STATUS = (400, 404, 413, 422)
TRANSIENT_STATUS = (408, 529)            # and every 5xx
ROLES = (("You are the batch analyst", "analyst"),
         ("You are a trajectory digester", "digester"),
         ("You are a harness engineer agent", "proposer"),
         ("You are a strict reviewer", "critic"))
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens")
MODEL_USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens",
                    "cacheCreationInputTokens")
DEFAULT_OVERHEAD_TOKENS = 4096           # A2; ~10x the 397 tokens measured on 2.1.311
DEFAULT_MAX_OUTPUT_TOKENS = 20_000       # upstream rrsi.llm.MAX_TOKENS
RC_LAUNCH, RC_TIMEOUT = -1, -9
HTTP_STATUS = {None: 200, "auth": 401, "limit": 429, "invalid_request": 400}
ERROR_KIND = {"auth": "authentication_error", "limit": "rate_limit_exceeded",
              "invalid_request": "invalid_request_error"}


def role_of(system: str) -> str:
    s = (system or "").lstrip()
    for prefix, role in ROLES:
        if s.startswith(prefix):
            return role
    return "other"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


def _int(x) -> int:
    try:
        return max(0, int(x or 0))
    except (TypeError, ValueError):
        return 0


def _err(message: str, kind: str) -> dict:
    return {"error": {"message": message, "type": kind, "code": kind}}


def _completion(n: int, model: str, text: str, usage: dict) -> dict:
    prompt_tokens = sum(int(usage.get(k) or 0) for k in USAGE_KEYS if k != "output_tokens")
    completion_tokens = int(usage.get("output_tokens") or 0)
    return {"id": f"chatcmpl-tcse-{n}", "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}}


def _parse_result(stdout: str):
    """The single JSON result object `claude -p --output-format json` prints."""
    if not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        for line in reversed(stdout.splitlines()):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def usage_totals(data: dict) -> dict | None:
    """Usage reported by one `claude -p` result, or None if it reports none.
    Each figure is the larger of `usage` and the per-model sum of `modelUsage`,
    so a request to an auxiliary model cannot hide from the budget."""
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    models = data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else {}
    if usage is None and not models:
        return None
    usage = usage or {}
    per_model = [m for m in models.values() if isinstance(m, dict)]
    u_in = sum(_int(usage.get(k)) for k in USAGE_KEYS if k != "output_tokens")
    u_out = _int(usage.get("output_tokens"))
    m_in = sum(_int(m.get(k)) for m in per_model for k in MODEL_USAGE_KEYS
               if k != "outputTokens")
    m_out = sum(_int(m.get("outputTokens")) for m in per_model)
    iterations = usage.get("iterations")
    n_iter = len(iterations) if isinstance(iterations, list) else 1
    return {"input": max(u_in, m_in), "output": max(u_out, m_out),
            "total": max(u_in + u_out, m_in + m_out),
            "requests": max(n_iter, len(models) or 1)}


class BudgetGuard:
    """Pre-call admission control for a token budget and a call cap (BUDGET in
    the module docstring). Thread safe, and it never calls a model itself, so it
    can front any backend."""

    def __init__(self, token_cap: int = 0, max_calls: int = 0,
                 overhead_tokens: int = DEFAULT_OVERHEAD_TOKENS):
        self.token_cap = _int(token_cap)
        self.max_calls = _int(max_calls)
        self.overhead_tokens = _int(overhead_tokens)
        self.lock = threading.Lock()
        self.halted: str | None = None
        self.halt_status = 429
        self.admitted = self.started = self.settled = self.in_flight = 0
        self.reserved = 0                 # reservations of admitted, unsettled calls
        self.charged = 0                  # what settled calls count against the budget
        self.reported = 0                 # usage settled calls reported (0 if unknown)
        self.unknown_usage_calls = 0
        self.overruns = 0
        self.max_overrun = 0
        self.peak_committed = 0           # max of charged + reserved after an admission

    def reservation(self, system: str, prompt: str, output_cap: int) -> int:
        return (self.overhead_tokens + len(system.encode("utf-8"))
                + len(prompt.encode("utf-8")) + _int(output_cap))

    def _halt(self, reason: str, status: int) -> None:      # caller holds the lock
        if not self.halted:
            self.halted, self.halt_status = reason, int(status)

    def halt(self, reason: str, status: int) -> None:
        with self.lock:
            self._halt(reason, status)

    def admit(self, reservation: int):
        """Reserve before a call: None if admitted, else (status, class, message)."""
        with self.lock:
            if self.halted:
                return self.halt_status, "halted_refused", f"adapter halted: {self.halted}"
            if self.max_calls and self.admitted >= self.max_calls:
                self._halt(f"call cap {self.max_calls} reached", 429)
                return 429, "call_cap_refused", f"adapter halted: {self.halted}"
            committed = self.charged + self.reserved
            if self.token_cap and committed + reservation > self.token_cap:
                self._halt(f"token budget {self.token_cap}: a call reserving {reservation} "
                           f"does not fit beside {committed} committed", 429)
                return 429, "budget_refused", f"adapter halted: {self.halted}"
            self.admitted += 1
            self.in_flight += 1
            self.reserved += reservation
            self.peak_committed = max(self.peak_committed, self.charged + self.reserved)
            return None

    def start(self, reservation: int) -> bool:
        """Last check before the process starts, after the parallelism wait:
        False, with the reservation released, if a halt happened meanwhile."""
        with self.lock:
            if self.halted:
                self.reserved -= reservation
                self.in_flight -= 1
                return False
            self.started += 1
            return True

    def settle(self, reservation: int, reported: int | None) -> dict:
        """Release the reservation, charge the call, and halt on an overrun."""
        with self.lock:
            self.reserved -= reservation
            self.in_flight -= 1
            self.settled += 1
            if reported is None:
                self.unknown_usage_calls += 1
                charge = reservation
            else:
                charge = reported
                self.reported += reported
            self.charged += charge
            overrun = max(0, (reported or 0) - reservation)
            if overrun:
                self.overruns += 1
                self.max_overrun = max(self.max_overrun, overrun)
                self._halt(f"reservation overrun: a call used {reported} tokens, "
                           f"{overrun} more than its reservation {reservation}", 429)
            return {"charged_tokens": charge, "overrun_tokens": overrun,
                    "cum_charged": self.charged, "cum_reported": self.reported,
                    "cum_reserved_in_flight": self.reserved}

    def state(self) -> dict:
        with self.lock:
            return {"token_cap": self.token_cap or None, "max_calls": self.max_calls or None,
                    "overhead_tokens": self.overhead_tokens, "admitted": self.admitted,
                    "started": self.started, "settled": self.settled,
                    "in_flight": self.in_flight, "reserved_in_flight": self.reserved,
                    "charged": self.charged, "reported": self.reported,
                    "unknown_usage_calls": self.unknown_usage_calls,
                    "peak_committed": self.peak_committed, "overruns": self.overruns,
                    "max_overrun": self.max_overrun,
                    "charged_within_cap": (not self.token_cap) or self.charged <= self.token_cap,
                    "halted": self.halted}


class Adapter:
    def __init__(self, claude_bin: str = "claude", ledger=None, dump_dir=None,
                 token_cap: int = 0, max_parallel: int = 3, timeout: int = 600,
                 max_calls: int = 0, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                 overhead_tokens: int = DEFAULT_OVERHEAD_TOKENS):
        self.claude_bin = claude_bin
        self.ledger = Path(ledger) if ledger else None
        self.dump_dir = Path(dump_dir) if dump_dir else None
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
        self.token_cap = _int(token_cap)
        self.max_output_tokens = max(1, _int(max_output_tokens))
        self.timeout = int(timeout)
        self.max_parallel = max(1, int(max_parallel))
        self.sem = threading.Semaphore(self.max_parallel)
        self.guard = BudgetGuard(token_cap, max_calls, overhead_tokens)
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.n = 0
        self.cost = 0.0
        raw = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        self._token_raw = raw
        self._token = "".join(raw.split()) if raw is not None else None
        self._secrets = sorted({s for s in (raw, (raw or "").strip(), self._token)
                                if s and len(s) >= 8}, key=len, reverse=True)

    @property
    def halted(self) -> str | None:
        return self.guard.halted

    @property
    def halt_status(self) -> int:
        return self.guard.halt_status

    @property
    def tokens(self) -> int:
        """Usage reported by settled calls (see guard.charged for the budget)."""
        return self.guard.reported

    def output_cap(self, body: dict) -> int:
        cap = self.max_output_tokens
        for key in ("max_tokens", "max_completion_tokens"):
            v = body.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                cap = min(cap, v)
        return cap

    def token_diagnostics(self) -> dict:
        """Non-secret facts about the token: never its value or any part of it."""
        raw, clean = self._token_raw, self._token or ""
        return {"present": bool(clean), "length": len(clean),
                "whitespace_removed": raw is not None and raw != clean,
                "setup_token_prefix": clean.startswith("sk-ant-oat"),
                "looks_like_api_key": clean.startswith("sk-ant-api")}

    # ------------------------------------------------------------ audit ---
    def scrub(self, s):
        if isinstance(s, str):
            for secret in self._secrets:
                s = s.replace(secret, "[redacted]")
        return s

    def _record(self, rec: dict) -> None:
        if not self.ledger:
            return
        line = self.scrub(json.dumps(rec, ensure_ascii=False))
        with self.io_lock, open(self.ledger, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _dump(self, n: int, role: str, **parts) -> None:
        if not self.dump_dir:
            return
        for name, text in parts.items():
            (self.dump_dir / f"{n:04d}_{role}_{name}").write_text(
                self.scrub(text or ""), encoding="utf-8")

    # ------------------------------------------------------------- core ---
    def _run(self, model: str, system: str, user: str, output_cap: int):
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        if self._token is not None:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self._token
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(int(output_cap))
        with tempfile.TemporaryDirectory(prefix="tcse-claude-") as td:
            work, cfg = Path(td) / "work", Path(td) / "config"
            work.mkdir()
            cfg.mkdir()
            sysfile = Path(td) / "system.txt"
            sysfile.write_text(system, encoding="utf-8")
            env["CLAUDE_CONFIG_DIR"] = str(cfg)
            cmd = [self.claude_bin, "-p", "--output-format", "json", "--model", model,
                   "--system-prompt-file", str(sysfile), "--tools", "",
                   "--disallowedTools", "mcp__*", "--strict-mcp-config",
                   "--max-turns", "1", "--no-session-persistence"]
            t0 = time.time()
            try:
                p = subprocess.run(cmd, input=user, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", cwd=work,
                                   env=env, timeout=self.timeout)
                rc, out, err = p.returncode, p.stdout or "", p.stderr or ""
            except subprocess.TimeoutExpired as e:
                rc = RC_TIMEOUT
                out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) \
                    else (e.stdout or "")
                err = f"adapter timeout after {self.timeout}s"
            except OSError as e:
                rc, out, err = RC_LAUNCH, "", f"could not start {self.claude_bin}: {e}"
            return rc, out, err, round(time.time() - t0, 2)

    def complete(self, body: dict) -> tuple[int, dict]:
        model = str(body.get("model") or "")
        msgs = body.get("messages") or []
        system = "\n\n".join(_text(m.get("content")) for m in msgs
                             if isinstance(m, dict) and m.get("role") == "system")
        user = "\n\n".join(_text(m.get("content")) for m in msgs
                           if isinstance(m, dict) and m.get("role") == "user")
        role = role_of(system)
        out_cap = self.output_cap(body)
        reservation = self.guard.reservation(system, user, out_cap)
        with self.lock:
            self.n += 1
            n = self.n
        base = {"n": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "role": role, "requested_model": model, "system_chars": len(system),
                "prompt_chars": len(user),
                "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
                "input_bytes": len(system.encode("utf-8")) + len(user.encode("utf-8")),
                "output_cap": out_cap, "reservation": reservation}

        def refuse(status: int, failure_class: str, message: str, kind: str):
            self._record({**base, "status": status, "invoked": False,
                          "failure_class": failure_class, "error": message})
            return status, _err(message, kind)

        def halt_kind(status: int) -> str:
            return "authentication_error" if status in AUTH_STATUS else "rate_limit_exceeded"

        if self.guard.halted:
            st = self.guard.halt_status
            return refuse(st, "halted_refused", f"adapter halted: {self.guard.halted}",
                          halt_kind(st))
        if not model or REFUSED_MODEL.search(model):
            return refuse(400, "model_refused",
                          f"model {model!r} refused by the zero-spend guard",
                          "invalid_request_error")
        if not user.strip():
            return refuse(400, "empty_prompt", "empty user prompt", "invalid_request_error")
        refusal = self.guard.admit(reservation)
        if refusal:
            st, cls, message = refusal
            return refuse(st, cls, message, halt_kind(st))

        with self.sem:
            if not self.guard.start(reservation):
                st = self.guard.halt_status
                return refuse(st, "halted_refused", f"adapter halted: {self.guard.halted}",
                              halt_kind(st))
            rc, out, err, wall = self._run(model, system, user, out_cap)
            # Settle and apply any halt while still holding the slot, so a call
            # queued behind this one sees the halt before it can start (G3).
            r = self._settle(rc, out, err, reservation, out_cap)
        failure, text, data, acct = r["failure"], r["text"], r["data"], r["acct"]
        status = HTTP_STATUS.get(failure, 502)
        self._record({**base, "status": status, "invoked": True, "returncode": rc,
                      "api_error_status": r["api_status"], "failure_class": failure,
                      "wall_s": wall, "is_error": bool(data.get("is_error")),
                      "subtype": data.get("subtype"), "stop_reason": data.get("stop_reason"),
                      "num_turns": data.get("num_turns"),
                      "duration_ms": data.get("duration_ms"),
                      "duration_api_ms": data.get("duration_api_ms"),
                      "served_models": r["served"], "usage": r["usage"],
                      "usage_known": r["reported"] is not None, "tokens": r["reported"] or 0,
                      **acct, "cum_tokens": acct["cum_reported"],
                      "assumption_violations": r["violations"],
                      "cost_estimate_usd": r["cost"], "cum_cost_estimate_usd": r["cum_cost"],
                      "reply_chars": len(text) if isinstance(text, str) else 0,
                      "reply_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()
                      if isinstance(text, str) else None,
                      "halted_after": r["halted_after"],
                      "error": None if failure is None else str(text or err or "no result")[:500]})
        self._dump(n, role, **{"system.txt": system, "prompt.txt": user,
                               "stdout.json": out, "stderr.txt": err})
        if failure is None:
            return 200, _completion(n, model, text, r["usage"])
        return status, _err(self.scrub(f"claude -p failed (rc={rc}, {failure}): "
                                       f"{str(text or err)[:300]}"),
                            ERROR_KIND.get(failure, "server_error"))

    def _settle(self, rc: int, out: str, err: str, reservation: int, out_cap: int) -> dict:
        """Classify one finished `claude -p` run, charge it to the budget and
        apply the halts it calls for."""
        data = _parse_result(out) or {}
        text = data.get("result")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        totals = usage_totals(data) if data else None
        # Nothing ran when the process could not start; otherwise unknown usage
        # (timeout, crash, garbage on stdout) is charged the whole reservation.
        reported = 0 if rc == RC_LAUNCH else (totals["total"] if totals else None)
        try:
            cost = float(data.get("total_cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        served = sorted((data.get("modelUsage") or {}).keys()) \
            if isinstance(data.get("modelUsage"), dict) else []
        ok = (rc == 0 and not data.get("is_error") and isinstance(text, str)
              and text.strip() != "")
        try:
            api_status = int(data["api_error_status"]) \
                if data.get("api_error_status") is not None else None
        except (TypeError, ValueError):
            api_status = None
        # Evidence is Claude Code's result message and stderr only: raw JSON
        # carries field names such as fallback_credit in every result.
        evidence = f"{text if isinstance(text, str) else ''} {err or ''}"
        if ok:
            failure = None
        elif rc == RC_LAUNCH:
            failure = "launch_error"
        elif rc == RC_TIMEOUT:
            failure = "timeout"
        elif not data:
            failure = "unparseable_output"
        elif api_status in AUTH_STATUS or AUTH_TEXT.search(evidence):
            failure = "auth"
        elif api_status in LIMIT_STATUS or LIMIT_TEXT.search(evidence):
            failure = "limit"
        elif api_status in INVALID_STATUS or (api_status is None
                                              and INVALID_TEXT.search(evidence)):
            failure = "invalid_request"
        elif api_status is not None and (api_status >= 500 or api_status in TRANSIENT_STATUS):
            failure = "transient"
        elif data.get("is_error"):
            failure = "cli_error"
        elif not (isinstance(text, str) and text.strip()):
            failure = "empty_result"
        else:
            failure = "other"

        acct = self.guard.settle(reservation, reported)
        violations = []
        if totals and rc != RC_LAUNCH:
            if totals["input"] > reservation - out_cap:
                violations.append("A1_A2_input_bound")
            if totals["requests"] > 1:
                violations.append("A3_single_request")
            if totals["output"] > out_cap:
                violations.append("A4_output_cap")
        if failure == "auth":
            self.guard.halt(f"authentication failed (HTTP {api_status})", 401)
        elif failure == "limit":
            self.guard.halt(f"usage limit or credit message from Claude Code "
                            f"(HTTP {api_status})", 429)
        refused_served = [m for m in served if REFUSED_MODEL.search(m)]
        if refused_served:
            self.guard.halt(f"refused model served: {refused_served}", 429)
        with self.lock:
            self.cost += cost
            cum_cost = round(self.cost, 6)
        return {"failure": failure, "text": text, "data": data, "usage": usage,
                "api_status": api_status, "served": served, "reported": reported,
                "acct": acct, "violations": violations, "cost": cost, "cum_cost": cum_cost,
                "halted_after": self.guard.halted}


def make_server(adapter: Adapter, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):      # keep public CI logs free of request lines
            return

        def _send(self, status: int, obj: dict) -> None:
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", "/v1/health"):
                self._send(200, {"ok": True, "calls": adapter.n, "tokens": adapter.tokens,
                                 "halted": adapter.halted, "budget": adapter.guard.state()})
            else:
                self._send(404, _err("not found", "not_found"))

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send(404, _err("not found", "not_found"))
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._send(400, _err("invalid JSON body", "invalid_request_error"))
                return
            if not isinstance(body, dict) or body.get("stream"):
                self._send(400, _err("only non-streaming JSON requests are supported",
                                     "invalid_request_error"))
                return
            try:
                status, obj = adapter.complete(body)
            except Exception as e:  # noqa: BLE001 - report, never crash the server
                status, obj = 500, _err(f"adapter error: {e!r}", "server_error")
            self._send(status, obj)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--claude-bin", default=os.environ.get("TCSE_CLAUDE_BIN", "claude"))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--dump-dir", default=None)
    ap.add_argument("--token-cap", type=int, default=0,
                    help="token budget enforced before each call by reservation (0: none)")
    ap.add_argument("--max-calls", type=int, default=0,
                    help="hard cap on claude invocations (0: none)")
    ap.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS,
                    help="per-call output ceiling, also lowered by a request's max_tokens")
    ap.add_argument("--overhead-tokens", type=int, default=DEFAULT_OVERHEAD_TOKENS,
                    help="declared bound on the input Claude Code adds to each call")
    ap.add_argument("--max-parallel", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("refusing to bind a non-loopback address")
    if min(a.token_cap, a.max_calls, a.overhead_tokens) < 0 or a.max_output_tokens < 1:
        sys.exit("refusing to start: budget values must be non-negative "
                 "(--max-output-tokens at least 1)")
    adapter = Adapter(a.claude_bin, a.ledger, a.dump_dir, a.token_cap, a.max_parallel,
                      a.timeout, max_calls=a.max_calls, max_output_tokens=a.max_output_tokens,
                      overhead_tokens=a.overhead_tokens)
    diag = adapter.token_diagnostics()
    print(f"[tcse-shim] token diagnostics (value never printed): {json.dumps(diag)}", flush=True)
    if diag["looks_like_api_key"]:
        sys.exit("refusing to start: CLAUDE_CODE_OAUTH_TOKEN holds an Anthropic API key, "
                 "not a subscription token from `claude setup-token`")
    smallest = a.overhead_tokens + 1 + 1        # one byte of prompt, one output token
    if a.token_cap and a.token_cap < smallest:
        print(f"[tcse-shim] WARNING: token budget {a.token_cap} is below the smallest "
              f"possible reservation ({smallest}); every call will be refused", flush=True)
    server = make_server(adapter, a.host, a.port)
    print(f"[tcse-shim] listening on http://{a.host}:{server.server_address[1]}/v1 "
          f"(pre-call admission: token budget {a.token_cap or 'none'}, call cap "
          f"{a.max_calls or 'none'}, output ceiling {a.max_output_tokens}, overhead bound "
          f"{a.overhead_tokens}; conservative admission control, not a provider-enforced "
          f"cap)", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
