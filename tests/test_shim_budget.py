# SPDX-License-Identifier: Apache-2.0
# TCSE fork addition; not part of upstream RRSI.
"""Offline regression gate for the adapter's pre-call budget (repaired 2026-11-16).

Standard library only and no real subprocess, so it runs on any OS:
    python -B -m unittest discover -s tests -p "test_shim_budget.py"
or under pytest with the rest of tests/. Claude Code is replaced by an
in-process fake runner that returns the result shape Claude Code 2.1.311
printed in run 43799511286 (usage, iterations, modelUsage, stop_reason).
No model, no network beyond 127.0.0.1, no secret.
"""

from __future__ import annotations

import importlib.util
import json
import random
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "tcse_claude_cli_shim_budget_gate", ROOT / "scripts" / "tcse_claude_cli_shim.py")
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)

# The exact preflight of run 43799511286: 54 + 22 = 76 bytes, 397 in + 4 out.
PREFLIGHT_SYSTEM = "TCSE preflight. Follow the user's instruction exactly."
PREFLIGHT_PROMPT = "Reply with exactly: OK"


def result(input_tokens=397, output_tokens=4, text="OK", is_error=False, api_status=None,
           iterations=1, models=(("claude-opus-5-5", None),), stop_reason="end_turn"):
    """A Claude Code `--output-format json` result. `models` holds (name, (in, out))
    pairs; None copies the main usage, as in the real result."""
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
             "fallback_credit": None, "iterations": [{"type": "message"}] * iterations}
    model_usage = {}
    for name, io in models:
        i, o = io if io is not None else (input_tokens, output_tokens)
        model_usage[name] = {"inputTokens": i, "outputTokens": o, "cacheReadInputTokens": 0,
                             "cacheCreationInputTokens": 0, "contextWindow": 1000000,
                             "maxOutputTokens": 128000}
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error,
                       "result": text, "api_error_status": api_status, "num_turns": 1,
                       "total_cost_usd": 0.0, "stop_reason": stop_reason, "usage": usage,
                       "modelUsage": model_usage})


def body(prompt=PREFLIGHT_PROMPT, system=PREFLIGHT_SYSTEM, model="opus", max_tokens=20000):
    b = {"model": model, "messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": prompt}]}
    if max_tokens is not None:
        b["max_tokens"] = max_tokens
    return b


class FakeRunner:
    """Stands in for Adapter._run. Records each process start with the guard's
    committed total at that instant; plan(k, system, user, cap) -> (rc, out, err)."""

    def __init__(self, adapter, plan, delay=0.0):
        self.adapter, self.plan, self.delay = adapter, plan, delay
        self.lock = threading.Lock()
        self.calls, self.active, self.max_active = [], 0, 0

    def __call__(self, model, system, user, output_cap):
        st = self.adapter.guard.state()
        with self.lock:
            k = len(self.calls)
            self.calls.append({"model": model, "output_cap": output_cap,
                               "committed_at_start": st["charged"] + st["reserved_in_flight"]})
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            rc, out, err = self.plan(k, system, user, output_cap)
            return rc, out, err, 0.0
        finally:
            with self.lock:
                self.active -= 1


def ok_plan(*_):
    return 0, result(), ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def adapter(self, plan=ok_plan, delay=0.0, **kw):
        a = shim.Adapter(claude_bin="claude-not-used", ledger=self.dir / "ledger.jsonl", **kw)
        a._run = FakeRunner(a, plan, delay)
        return a

    def ledger(self):
        p = self.dir / "ledger.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] \
            if p.exists() else []

    @staticmethod
    def burst(a, bodies):
        out = [None] * len(bodies)
        gate = threading.Barrier(len(bodies))

        def go(i):
            gate.wait()
            out[i] = a.complete(bodies[i])
        threads = [threading.Thread(target=go, args=(i,)) for i in range(len(bodies))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return out


class AdmissionBeforeTheCall(Base):
    def test_one_token_cap_refuses_before_any_invocation(self):
        # Run 43799511286 at d155b43: --token-cap 1 still spent 401 tokens, because
        # the cap was compared after the call. Now nothing may start.
        a = self.adapter(token_cap=1)
        st, obj = a.complete(body())
        self.assertEqual(st, 429)
        self.assertIn("halted", obj["error"]["message"])
        self.assertEqual(a._run.calls, [])
        rec = self.ledger()[0]
        self.assertEqual((rec["invoked"], rec["failure_class"]), (False, "budget_refused"))
        self.assertEqual(rec["input_bytes"], 76)
        self.assertEqual(rec["reservation"], shim.DEFAULT_OVERHEAD_TOKENS + 76 + 20000)
        self.assertIn("token budget 1", a.halted)
        st, _ = a.complete(body())
        self.assertEqual((st, self.ledger()[1]["failure_class"]), (429, "halted_refused"))
        self.assertEqual(a._run.calls, [])
        self.assertEqual(a.guard.state()["charged"], 0)

    def test_token_check_mode_allows_exactly_one_call(self):
        a = self.adapter(token_cap=30000, max_calls=1)
        self.assertEqual(a.complete(body())[0], 200)
        self.assertEqual(a.complete(body())[0], 429)
        self.assertEqual(a.complete(body())[0], 429)
        self.assertEqual(len(a._run.calls), 1)
        self.assertEqual([r["failure_class"] for r in self.ledger()],
                         [None, "call_cap_refused", "halted_refused"])
        self.assertEqual(self.ledger()[0]["charged_tokens"], 401)
        self.assertIn("call cap 1", a.halted)

    def test_output_ceiling_is_the_smaller_of_request_and_flag(self):
        a = self.adapter(max_output_tokens=1000)
        a.complete(body(max_tokens=20000))
        a.complete(body(max_tokens=256))
        a.complete(body(max_tokens=None))
        self.assertEqual([c["output_cap"] for c in a._run.calls], [1000, 256, 1000])
        self.assertEqual([r["reservation"] for r in self.ledger()],
                         [shim.DEFAULT_OVERHEAD_TOKENS + 76 + x for x in (1000, 256, 1000)])

    def test_refused_and_empty_requests_never_reserve(self):
        a = self.adapter(token_cap=10**9)
        self.assertEqual(a.complete(body(model="claude-fable-5-2"))[0], 400)
        self.assertEqual(a.complete(body(prompt="   "))[0], 400)
        self.assertEqual([r["failure_class"] for r in self.ledger()],
                         ["model_refused", "empty_prompt"])
        s = a.guard.state()
        self.assertEqual((s["admitted"], s["reserved_in_flight"], a._run.calls), (0, 0, []))


class Concurrency(Base):
    def test_burst_cannot_overshoot_the_budget(self):
        # Worst case the assumptions allow: every call uses exactly its reservation.
        R = shim.DEFAULT_OVERHEAD_TOKENS + 76 + 20000

        def worst(_k, _s, _u, cap):
            return 0, result(input_tokens=R - cap, output_tokens=cap), ""
        a = self.adapter(worst, delay=0.05, token_cap=3 * R + 10, max_parallel=8)
        res = self.burst(a, [body() for _ in range(8)])
        self.assertEqual(sorted(st for st, _ in res).count(200), 3)
        self.assertEqual(len(a._run.calls), 3)
        s = a.guard.state()
        self.assertLessEqual(s["peak_committed"], 3 * R + 10)
        self.assertLessEqual(s["charged"], 3 * R + 10)
        self.assertTrue(all(c["committed_at_start"] <= 3 * R + 10 for c in a._run.calls))
        self.assertEqual((s["in_flight"], s["reserved_in_flight"], s["overruns"]), (0, 0, 0))
        recs = self.ledger()
        self.assertEqual(len(recs), 8)
        self.assertEqual(sum(r["invoked"] for r in recs), 3)
        self.assertTrue(all(not r["invoked"] for r in recs
                            if r["failure_class"] in ("budget_refused", "halted_refused")))

    def test_queued_calls_do_not_start_after_a_halt(self):
        # d155b43 checked the halt before the parallelism wait only, so requests
        # queued behind a failing call still ran. Now they are re-checked.
        release, started = threading.Event(), threading.Event()

        def plan(k, *_):
            if k == 0:
                started.set()
                release.wait(10)
                return 1, result(0, 0, "Failed to authenticate. API Error: 401", True, 401), ""
            return 0, result(), ""
        a = self.adapter(plan, max_parallel=1)
        first = threading.Thread(target=a.complete, args=(body(),))
        first.start()
        self.assertTrue(started.wait(10))
        rest = [threading.Thread(target=a.complete, args=(body(),)) for _ in range(3)]
        for t in rest:
            t.start()
        deadline = time.time() + 10
        while a.guard.state()["admitted"] < 4 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(a.guard.state()["admitted"], 4)     # all four passed admission
        release.set()
        for t in [first] + rest:
            t.join(10)
        self.assertEqual(len(a._run.calls), 1)
        self.assertEqual(sorted(r["failure_class"] for r in self.ledger()),
                         ["auth", "halted_refused", "halted_refused", "halted_refused"])
        s = a.guard.state()
        self.assertEqual((s["started"], s["in_flight"], s["reserved_in_flight"]), (1, 0, 0))
        self.assertEqual(a.halted, "authentication failed (HTTP 401)")

    def test_random_schedules_keep_the_admission_invariant(self):
        rng = random.Random(20261116)
        for trial in range(5):
            cap = rng.randint(60_000, 400_000)
            prompts = ["p" * rng.randint(1, 30_000) for _ in range(40)]
            caps = [rng.choice([None, 512, 4000, 20000]) for _ in range(40)]

            def plan(_k, system, user, out_cap, rng=random.Random(trial)):
                bound = shim.DEFAULT_OVERHEAD_TOKENS + len(system) + len(user)
                return 0, result(rng.randint(0, bound), rng.randint(0, out_cap)), ""
            a = self.adapter(plan, delay=0.002, token_cap=cap,
                             max_parallel=rng.randint(1, 6))
            for i in range(0, 40, 10):
                self.burst(a, [body(prompt=p, max_tokens=c)
                               for p, c in zip(prompts[i:i + 10], caps[i:i + 10])])
            s = a.guard.state()
            self.assertLessEqual(s["peak_committed"], cap)
            self.assertLessEqual(s["charged"], cap)
            self.assertTrue(all(c["committed_at_start"] <= cap for c in a._run.calls))
            self.assertEqual((s["in_flight"], s["reserved_in_flight"]), (0, 0))
            self.assertEqual(s["started"], len(a._run.calls))
            self.assertEqual(sum(r["invoked"] for r in self.ledger()), len(a._run.calls))
            (self.dir / "ledger.jsonl").unlink(missing_ok=True)


class Settlement(Base):
    def test_unknown_usage_is_charged_the_full_reservation(self):
        plans = [(shim.RC_TIMEOUT, "", "adapter timeout after 600s"),
                 (0, "not json at all", ""),
                 (2, "", "segfault")]
        a = self.adapter(lambda k, *_: plans[k])
        R = shim.DEFAULT_OVERHEAD_TOKENS + 76 + 20000
        self.assertEqual([a.complete(body())[0] for _ in plans], [502, 502, 502])
        recs = self.ledger()
        self.assertEqual([r["failure_class"] for r in recs],
                         ["timeout", "unparseable_output", "unparseable_output"])
        self.assertEqual([r["charged_tokens"] for r in recs], [R, R, R])
        self.assertEqual([r["usage_known"] for r in recs], [False, False, False])
        self.assertEqual(a.guard.state()["unknown_usage_calls"], 3)
        self.assertIsNone(a.halted)

    def test_launch_failure_charges_nothing(self):
        a = self.adapter(lambda *_: (shim.RC_LAUNCH, "", "could not start claude"))
        self.assertEqual(a.complete(body())[0], 502)
        rec = self.ledger()[0]
        self.assertEqual((rec["failure_class"], rec["charged_tokens"]), ("launch_error", 0))

    def test_overrun_is_detected_and_halts(self):
        a = self.adapter(lambda *_: (0, result(4000, 30000), ""))
        self.assertEqual(a.complete(body())[0], 200)
        rec = self.ledger()[0]
        R = shim.DEFAULT_OVERHEAD_TOKENS + 76 + 20000
        self.assertEqual(rec["overrun_tokens"], 34000 - R)
        self.assertIn("A4_output_cap", rec["assumption_violations"])
        self.assertIn("reservation overrun", a.halted)
        self.assertEqual(a.complete(body())[0], 429)
        self.assertEqual(len(a._run.calls), 1)
        s = a.guard.state()
        self.assertEqual((s["overruns"], s["max_overrun"]), (1, 34000 - R))

    def test_auxiliary_model_usage_is_counted_and_flagged(self):
        aux = (("claude-opus-5-5", None), ("claude-haiku-5-5", (1000, 50)))
        a = self.adapter(lambda *_: (0, result(397, 4, models=aux), ""))
        self.assertEqual(a.complete(body())[0], 200)
        rec = self.ledger()[0]
        self.assertEqual(rec["charged_tokens"], 397 + 4 + 1050)
        self.assertEqual(rec["assumption_violations"], ["A3_single_request"])
        self.assertIsNone(a.halted)

    def test_measured_preflight_satisfies_every_assumption(self):
        a = self.adapter()
        self.assertEqual(a.complete(body())[0], 200)
        rec = self.ledger()[0]
        self.assertEqual((rec["tokens"], rec["charged_tokens"], rec["assumption_violations"]),
                         (401, 401, []))
        self.assertEqual(rec["stop_reason"], "end_turn")

    def test_refused_model_served_halts(self):
        served = (("claude-fable-5-2", None),)
        a = self.adapter(lambda *_: (0, result(models=served), ""))
        self.assertEqual(a.complete(body())[0], 200)
        self.assertIn("refused model served", a.halted)
        self.assertEqual(a.complete(body())[0], 429)


class FailureClasses(Base):
    CASES = [  # (rc, api status, result text, is_error) -> (class, HTTP status, halts)
        ((1, 401, "Failed to authenticate. API Error: 401 Invalid bearer token", True),
         ("auth", 401, True)),
        ((1, 429, "Claude usage limit reached. Your limit will reset at 5pm.", True),
         ("limit", 429, True)),
        ((1, 400, "Prompt is too long", True), ("invalid_request", 400, False)),
        ((1, None, "API Error: prompt is too long: 1200000 tokens", True),
         ("invalid_request", 400, False)),
        ((1, 529, "API Error: 529 Overloaded", True), ("transient", 502, False)),
        ((1, 500, "server error", True), ("transient", 502, False)),
        ((1, None, "Reached max turns", True), ("cli_error", 502, False)),
        ((0, None, "   ", False), ("empty_result", 502, False)),
    ]

    def test_each_failure_maps_to_one_class_and_status(self):
        for (rc, api, text, is_err), (cls, status, halts) in self.CASES:
            with self.subTest(cls=cls, text=text):
                (self.dir / "ledger.jsonl").unlink(missing_ok=True)
                out = result(0, 0, text, is_err, api)
                a = self.adapter(lambda *_, out=out, rc=rc: (rc, out, ""))
                st, obj = a.complete(body())
                self.assertEqual((st, self.ledger()[0]["failure_class"]), (status, cls))
                self.assertEqual(a.halted is not None, halts)
                if cls == "invalid_request":
                    self.assertEqual(obj["error"]["type"], "invalid_request_error")

    def test_credit_field_alone_is_never_a_limit(self):
        # Regression kept from d155b43: every result carries "fallback_credit".
        a = self.adapter(lambda *_: (1, result(0, 0, "API Error: 529 Overloaded", True, 529), ""))
        self.assertEqual(a.complete(body())[0], 502)
        self.assertIsNone(a.halted)


class ChildProcessAndHealth(Base):
    def test_output_ceiling_and_isolation_reach_the_child_process(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(cmd=cmd, env=kw["env"], input=kw["input"])
            return subprocess.CompletedProcess(cmd, 0, stdout=result(), stderr="")
        a = shim.Adapter(claude_bin="claude", ledger=self.dir / "ledger.jsonl",
                         max_output_tokens=4000)
        real = shim.subprocess.run
        shim.subprocess.run = fake_run
        try:
            with mock.patch.dict(shim.os.environ, {"ANTHROPIC_API_KEY": "strip-me",
                                                   "CLAUDE_CODE_USE_VERTEX": "1"}):
                st, _ = a.complete(body(max_tokens=512))
        finally:
            shim.subprocess.run = real
        self.assertEqual(st, 200)
        self.assertEqual(seen["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "512")
        self.assertNotIn("ANTHROPIC_API_KEY", seen["env"])
        self.assertNotIn("CLAUDE_CODE_USE_VERTEX", seen["env"])
        self.assertEqual(seen["input"], PREFLIGHT_PROMPT)
        self.assertEqual(seen["cmd"][seen["cmd"].index("--max-turns") + 1], "1")
        self.assertEqual(self.ledger()[0]["reservation"],
                         shim.DEFAULT_OVERHEAD_TOKENS + 76 + 512)

    def test_health_reports_the_budget_state(self):
        a = self.adapter(token_cap=30000, max_calls=1)
        server = shim.make_server(a, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            req = urllib.request.Request(base + "/v1/chat/completions",
                                         data=json.dumps(body()).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                self.assertEqual(r.status, 200)
            with urllib.request.urlopen(base + "/health", timeout=30) as r:
                health = json.loads(r.read())
        finally:
            server.shutdown()
            server.server_close()
        b = health["budget"]
        self.assertEqual((health["calls"], health["tokens"]), (1, 401))
        self.assertEqual((b["token_cap"], b["max_calls"], b["started"], b["charged"]),
                         (30000, 1, 1, 401))
        self.assertTrue(b["charged_within_cap"])

    def test_startup_rejects_negative_budget_values(self):
        for argv in (["--token-cap", "-1"], ["--max-calls", "-2"], ["--max-output-tokens", "0"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                shim.main(["--port", "0"] + argv)


if __name__ == "__main__":
    unittest.main()
