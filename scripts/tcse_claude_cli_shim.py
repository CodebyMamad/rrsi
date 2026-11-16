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
  * a usage-limit or credit message from Claude Code halts the adapter: every
    later request fails fast with HTTP 429 instead of retrying;
  * an optional cumulative token cap halts the adapter the same way.
The account-side guard (usage credits switched off) is the owner's.

Every call is recorded in a JSONL ledger; prompts, system texts and raw
Claude Code output can be dumped for audit. The token is scrubbed from
everything written to disk.
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
LIMIT_TEXT = re.compile(r"usage limit|limit reached|hit your limit|limit will reset|"
                        r"rate.?limit|quota|out of (extra )?usage|credit", re.I)
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
        self._secret = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or ""

    # ------------------------------------------------------------ audit ---
    def scrub(self, s):
        if isinstance(s, str) and len(self._secret) >= 8:
            return s.replace(self._secret, "[redacted]")
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
            n, halted = self.n, self.halted
        base = {"n": n, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "role": role, "requested_model": model, "system_chars": len(system),
                "prompt_chars": len(user),
                "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest()}
        if halted:
            self._record({**base, "status": 429, "invoked": False, "error": f"halted: {halted}"})
            return 429, _err(f"adapter halted: {halted}", "rate_limit_exceeded")
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
        limit_hit = (not ok) and bool(LIMIT_TEXT.search(f"{text or ''} {err or ''} {out[-2000:]}"))
        with self.lock:
            self.tokens += tokens
            self.cost += cost
            cum_tokens, cum_cost = self.tokens, round(self.cost, 6)
            if limit_hit and not self.halted:
                self.halted = "usage limit or credit message from Claude Code"
            if self.token_cap and cum_tokens >= self.token_cap and not self.halted:
                self.halted = f"token cap {self.token_cap} reached"
            halted_now = self.halted
        status = 200 if ok else (429 if limit_hit else 502)
        self._record({**base, "status": status, "invoked": True, "returncode": rc,
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
        kind = "rate_limit_exceeded" if limit_hit else "server_error"
        return status, _err(f"claude -p failed (rc={rc}): {str(text or err)[:300]}", kind)


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
    server = make_server(adapter, a.host, a.port)
    print(f"[tcse-shim] listening on http://{a.host}:{server.server_address[1]}/v1 "
          f"(token cap {a.token_cap or 'none'})", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
