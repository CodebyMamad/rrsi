# Copyright 2026 The rrsi Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Claude client for the three search roles (proposer, analyst, critic).

Claude Opus 4.8 via AnthropicVertex, round-robin over the configured GCP
projects with retry-and-rotate on failure. `cache_prefix` sends a large stable
leading block (constitution + harness source) as its own ephemeral-cached
content block so repeated turns pay to read it once.
"""

from __future__ import annotations

import itertools
import os
import re
import threading
import time

MODEL = os.environ.get("RRSI_SEARCH_MODEL", "claude-opus-4-8")
_PROJECTS = [
    {"project_id": p.strip(), "region": os.environ.get("RRSI_VERTEX_REGION", "global")}
    for p in os.environ.get("RRSI_VERTEX_PROJECTS", "").split(",")
    if p.strip()
]
MAX_TOKENS = 20_000

_clients: dict = {}
_clients_lock = threading.Lock()
_rr = itertools.count()
_JSON_SUFFIX = ("\n\nOutput ONLY a single valid JSON object. No prose before or "
                "after, no markdown fences.")


def _client_for(idx: int):
    from anthropic import AnthropicVertex
    if not _PROJECTS:
        raise RuntimeError("set RRSI_VERTEX_PROJECTS to a comma-separated list of GCP "
                           "projects with Claude on Vertex AI enabled")
    with _clients_lock:
        c = _clients.get(idx)
        if c is None:
            c = AnthropicVertex(**_PROJECTS[idx])
            _clients[idx] = c
        return c


def extract_json(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith("{") and not t.startswith("["):
        start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
        if start != -1:
            t = t[start:]
    if t and t[0] == "{" and not t.endswith("}"):
        end = t.rfind("}")
        if end != -1:
            t = t[:end + 1]
    if t and t[0] == "[" and not t.endswith("]"):
        end = t.rfind("]")
        if end != -1:
            t = t[:end + 1]
    return t


def _generate_with_litellm(prompt: str, system: str | None, max_retries: int,
                           json_only: bool, model: str, max_tokens: int,
                           cache_prefix: str | None) -> str:
    """Provider-neutral research roles; opt-in Vertex legacy path remains available."""
    from litellm import completion
    if not model:
        raise ValueError("Set RRSI_SEARCH_MODEL or per-role model env variable")
    prompt_text = ((cache_prefix + "\n\n" + prompt) if cache_prefix else prompt)
    messages = []
    if system or json_only:
        messages.append({"role": "system",
                         "content": (system or "") + (_JSON_SUFFIX if json_only else "")})
    messages.append({"role": "user", "content": prompt_text})
    limit = int(os.environ.get("RRSI_SEARCH_MAX_TOKENS", "8192"))
    if limit <= 0:
        raise ValueError("RRSI_SEARCH_MAX_TOKENS must be positive")
    kwargs = {"model": model, "messages": messages,
              "max_tokens": min(max_tokens, limit)}
    api_base = os.environ.get("RRSI_SEARCH_API_BASE")
    api_key = os.environ.get("RRSI_SEARCH_API_KEY")
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    last_error = None
    for attempt in range(max_retries):
        try:
            result = completion(**kwargs)
            content = result.choices[0].message.content
            if isinstance(content, str) and content.strip():
                return extract_json(content) if json_only else content
            last_error = RuntimeError("Empty non-text LLM response")
        except Exception as exc:
            last_error = exc
        if attempt + 1 < max_retries:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"LiteLLM call failed after {max_retries} attempts: {last_error}")


def generate(prompt: str, system: str | None = None, max_retries: int = 6,
             json_only: bool = False, model: str | None = None,
             max_tokens: int = MAX_TOKENS, cache_prefix: str | None = None) -> str:
    mdl = model or MODEL
    backend = os.environ.get("RRSI_SEARCH_BACKEND", "litellm").lower()
    if backend == "litellm":
        return _generate_with_litellm(prompt, system, max_retries, json_only,
                                      mdl, max_tokens, cache_prefix)
    if backend != "vertex":
        raise ValueError(f"Unsupported RRSI_SEARCH_BACKEND: {backend}")
    sys_prompt = (system or "") + (_JSON_SUFFIX if json_only else "")
    content = ([{"type": "text", "text": cache_prefix,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": prompt}] if cache_prefix else prompt)
    n = len(_PROJECTS)
    start = next(_rr)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        idx = (start + attempt) % n
        try:
            client = _client_for(idx)
            kwargs = {"model": mdl, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": content}]}
            if sys_prompt:
                kwargs["system"] = sys_prompt
            resp = client.messages.create(**kwargs)
            text = "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text")
            if text:
                return extract_json(text) if json_only else text
            last_err = RuntimeError("empty response")
        except Exception as e:  # noqa: BLE001 - rotate to the other project
            last_err = e
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"generate failed after {max_retries} tries: {last_err}")


if __name__ == "__main__":
    print(generate("Reply with exactly: OK", max_tokens=16))
