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
  * Fable and Mythos models are refused (on some plans they bill usage credits);
  * failures are classified from Claude Code's own `api_error_status` and its
    result message, never from raw JSON (every result carries field names
    such as `fallback_credit`):
      - 401/403 or an authentication message: halt, later requests get 401;
      - 402/429 or a usage-limit/credit message: halt, later requests get 429;
      - anything else (e.g. 529 overloaded): HTTP 502 for that call only;
  * an optional cumulative token cap halts the adapter the same way.
The account-side guard (usage credits switched off) is the owner's.

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
AUTH_STATUS = (401, 403)
LIMIT_STATUS = (402, 429)
ROLES = (("You are the batch analyst", "analyst"),
         ("You are a trajectory digester", "digester"),
         ("You are a harness engineer agent", "proposer"),
         ("You are a strict reviewer", "critic"))
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
              "cache_read_input_tokens")


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


class Adapter:
    def __init__(self, claude_bin: str = "claude", ledger=None, dump_dir=None,
                 token_cap: int = 0, max_parallel: int = 3, timeout: int = 600):
        self.claude_bin = claude_bin
        self.ledger = Path(ledger) if ledger else None
        self.dump_dir = Path(dump_dir) if dump_dir else None
        if self.dump_dir:
            self.dump_dir.mkdir(parents=True, exist_ok=True)
        self.token_cap = int(token_cap or 0)
        self.timeout = int(timeout)
        self.sem = threading.Semaphore(max(1, int(max_parallel)))
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()
        self.n = 0
        self.tokens = 0
        self.cost = 0.0
        self.halted: str | None = None
        self.halt_status = 429
        raw = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        self._token_raw = raw
        self._token = "".join(raw.split()) if raw is not None else None
        self._secrets = sorted({s for s in (raw, (raw or "").strip(), self._token)
                                if s and len(s) >= 8}, key=len, reverse=True)

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
    def _run(self, model: str, system: str, user: str):
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        if self._token is not None:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self._token
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
                rc = -9
                out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) \
                    else (e.stdout or "")
                err = f"adapter timeout after {self.timeout}s"
            except OSError as e:
                rc, out, err = -1, "", f"could not start {self.claude_bin}: {e}"
            return rc, out, err, round(time.time() - t0, 2)

    def complete(self, body: dict) -> tuple[int, dict]:
        model = str(body.get("model") or "")
        msgs = body.get("messages") or []
        system = "\n\n".join(_text(m.get("content")) for m in msgs
                             if isinstance(m, dict) and m.get("role") == "system")
        user = "\n\n".join(_text(m.get("content")) for m in msgs
                           if isinstance(m, dict) and m.get("role") == "user")
        role = role_of(system)
        with self.lock:
            self.n += 1
            n, halted, halt_status = self.n, self.halted, self.halt_status
        base = {"n": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "role": role, "requested_model": model, "system_chars": len(system),
                "prompt_chars": len(user),
                "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest()}
        if halted:
            self._record({**base, "status": halt_status, "invoked": False,
                          "error": f"halted: {halted}"})
            kind = "authentication_error" if halt_status in AUTH_STATUS else "rate_limit_exceeded"
            return halt_status, _err(f"adapter halted: {halted}", kind)
        if not model or REFUSED_MODEL.search(model):
            self._record({**base, "status": 400, "invoked": False, "error": "model refused"})
            return 400, _err(f"model {model!r} refused by the zero-spend guard",
                             "invalid_request_error")
        if not user.strip():
            self._record({**base, "status": 400, "invoked": False, "error": "empty prompt"})
            return 400, _err("empty user prompt", "invalid_request_error")

        with self.sem:
            rc, out, err, wall = self._run(model, system, user)
        data = _parse_result(out) or {}
        text = data.get("result")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tokens = sum(int(usage.get(k) or 0) for k in USAGE_KEYS)
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
        auth_fail = (not ok) and (api_status in AUTH_STATUS or bool(AUTH_TEXT.search(evidence)))
        limit_hit = (not ok) and not auth_fail and (
            api_status in LIMIT_STATUS or bool(LIMIT_TEXT.search(evidence)))
        with self.lock:
            self.tokens += tokens
            self.cost += cost
            cum_tokens, cum_cost = self.tokens, round(self.cost, 6)
            if auth_fail and not self.halted:
                self.halted = f"authentication failed (HTTP {api_status})"
                self.halt_status = 401
            if limit_hit and not self.halted:
                self.halted = f"usage limit or credit message from Claude Code (HTTP {api_status})"
                self.halt_status = 429
            if self.token_cap and cum_tokens >= self.token_cap and not self.halted:
                self.halted = f"token cap {self.token_cap} reached"
                self.halt_status = 429
            halted_now = self.halted
        status = 200 if ok else (401 if auth_fail else (429 if limit_hit else 502))
        self._record({**base, "status": status, "invoked": True, "returncode": rc,
                      "api_error_status": api_status, "failure_class":
                      None if ok else ("auth" if auth_fail else ("limit" if limit_hit else "other")),
                      "wall_s": wall, "is_error": bool(data.get("is_error")),
                      "subtype": data.get("subtype"), "num_turns": data.get("num_turns"),
                      "duration_ms": data.get("duration_ms"),
                      "duration_api_ms": data.get("duration_api_ms"),
                      "served_models": served, "usage": usage, "tokens": tokens,
                      "cost_estimate_usd": cost, "cum_tokens": cum_tokens,
                      "cum_cost_estimate_usd": cum_cost,
                      "reply_chars": len(text) if isinstance(text, str) else 0,
                      "reply_sha256": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
                      "halted_after": halted_now,
                      "error": None if ok else str(text or err or "no result")[:500]})
        self._dump(n, role, **{"system.txt": system, "prompt.txt": user,
                               "stdout.json": out, "stderr.txt": err})
        if ok:
            return 200, _completion(n, model, text, usage)
        kind = ("authentication_error" if auth_fail else
                ("rate_limit_exceeded" if limit_hit else "server_error"))
        return status, _err(self.scrub(f"claude -p failed (rc={rc}): {str(text or err)[:300]}"),
                            kind)


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
                                 "halted": adapter.halted})
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
    ap.add_argument("--token-cap", type=int, default=0)
    ap.add_argument("--max-parallel", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=600)
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("refusing to bind a non-loopback address")
    adapter = Adapter(a.claude_bin, a.ledger, a.dump_dir, a.token_cap, a.max_parallel, a.timeout)
    diag = adapter.token_diagnostics()
    print(f"[tcse-shim] token diagnostics (value never printed): {json.dumps(diag)}", flush=True)
    if diag["looks_like_api_key"]:
        sys.exit("refusing to start: CLAUDE_CODE_OAUTH_TOKEN holds an Anthropic API key, "
                 "not a subscription token from `claude setup-token`")
    server = make_server(adapter, a.host, a.port)
    print(f"[tcse-shim] listening on http://{a.host}:{server.server_address[1]}/v1 "
          f"(token cap {a.token_cap or 'none'})", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
