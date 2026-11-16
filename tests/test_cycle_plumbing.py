# SPDX-License-Identifier: Apache-2.0
# TCSE fork addition; not part of upstream RRSI.
"""P1: full-cycle plumbing gate for the UNMODIFIED RRSI loop.

PLUMBING-ONLY. Every model reply, every reward, every token count and the
domain itself are synthetic. Nothing here measures whether RRSI improves a
harness, and nothing here reproduces the paper. The question is only whether
the released control flow in rrsi/ (frozen commit be50316) executes end to
end without any unpublished component:

    baseline -> calibrate -> round 0: analyze (analyst + digesters)
    -> propose x2 in git worktrees -> critic (precheck reject -> repair
    -> accept) -> commit -> candidate smoke -> evaluate -> select (Alg. 2)
    -> fast-forward evolve/<domain> -> persist -> round 1 (smoke_fail and
    noise-floor reject -> H_2 = H_1); plus crash-and-resume of a round.

Synthetic: StubDomain (a trial's reward is a deterministic function of the
LEVEL constant in the harness file of the worktree being evaluated) and
FakeLLM (scripted JSON replies patched into rrsi.analyst, rrsi.digester,
rrsi.propose and rrsi.critic). Original: everything imported from rrsi/.

No network, no model API, no Docker, no Harbor; a temporary git repo only.
Set RRSI_PLUMBING_RECEIPT_DIR to keep a receipt of every run's state.

    python -m pytest -q tests/test_cycle_plumbing.py
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rrsi.analyst as analyst_mod          # noqa: E402
import rrsi.critic as critic_mod            # noqa: E402
import rrsi.digester as digester_mod        # noqa: E402
import rrsi.llm as llm_mod                  # noqa: E402
import rrsi.propose as propose_mod          # noqa: E402
from rrsi import gitops as G                # noqa: E402
from rrsi.config import RRSIConfig          # noqa: E402
from rrsi.domain import Domain              # noqa: E402
from rrsi.evaluate import TaskResult        # noqa: E402
from rrsi.loop import Run                   # noqa: E402
from rrsi.schedule import edit_budget       # noqa: E402

FROZEN_BASE = "be50316e1db05914068a973f322770ef08ed7ba1"
TASKS = ["t1", "t2", "t3", "t4", "t5", "t6"]
H0_SOURCE = '"""Stub harness (plumbing-only)."""\nLEVEL = 1\n'
RECEIPT_ROOT = os.environ.get("RRSI_PLUMBING_RECEIPT_DIR")


class SimulatedCrash(BaseException):
    """Stands in for a killed process. A BaseException, so the loop's
    `except Exception` handlers cannot absorb it."""


# --------------------------------------------------------------- domain ---
class StubDomain(Domain):
    """Synthetic benchmark: task t_i passes iff i <= LEVEL, where LEVEL is
    read from the harness of the worktree being evaluated. Every trial costs
    1000 tokens. Selection therefore depends on the committed diff alone."""

    name = "stub"
    harness_path = "harness"
    source_exts = {".py", ".txt", ".md", ".json"}
    critic_patterns = [(r"PLANTED_LEAK_MARKER", "planted leak marker in diff")]
    component_signals = [("config", [r"LEVEL\s*="])]
    briefs = {"analyst": "STUB-ANALYST-BRIEF (plumbing-only)",
              "digester": "STUB-DIGESTER-BRIEF (plumbing-only)",
              "proposer": "STUB-PROPOSER-BRIEF (plumbing-only)",
              "critic": "STUB-CRITIC-BRIEF (plumbing-only)"}

    def __init__(self):
        self.run_calls: list[dict] = []
        self.smoke_calls: list[dict] = []
        self.crash_on_job: str | None = None
        self._lock = threading.Lock()

    def evolve_ids(self):
        return list(TASKS)

    def smoke_ids(self, incumbent_per_task=None):
        return ["t1"]

    @staticmethod
    def level(root) -> int:
        src = (Path(root) / "domains" / "stub" / "harness" / "agent.py").read_text()
        for node in ast.parse(src).body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "LEVEL"):
                return int(ast.literal_eval(node.value))
        raise ValueError("stub harness defines no LEVEL")

    def run(self, root, runs_dir, job, ids, k, log_prefix=""):
        level = self.level(root)
        crash = self.crash_on_job == job
        with self._lock:
            self.run_calls.append({"job": job, "ids": list(ids), "k": k, "level": level,
                                   "root": Path(root).name, "crashed": crash})
        if crash:
            self.crash_on_job = None
            raise SimulatedCrash(f"simulated kill while evaluating {job}")
        jdir = Path(runs_dir) / "jobs" / job
        jdir.mkdir(parents=True, exist_ok=True)
        for tid in ids:
            reward = 1.0 if TASKS.index(tid) + 1 <= level else 0.0
            for j in range(k):
                (jdir / f"{tid}__{j}.json").write_text(json.dumps(
                    {"task_id": tid, "trial": j, "reward": reward, "tokens": 1000,
                     "level": level}))

    def score(self, runs_dir, job, ids, k):
        jdir = Path(runs_dir) / "jobs" / job
        per, passes = {}, 0
        for tid in ids:
            rewards, toks = [], []
            for j in range(k):
                p = jdir / f"{tid}__{j}.json"
                if p.exists():
                    rec = json.loads(p.read_text())
                    rewards.append(float(rec["reward"]))
                    toks.append(rec["tokens"])
            missing = k - len(rewards)
            rewards += [0.0] * missing
            toks += [None] * missing
            passes += sum(1 for x in rewards if x == 1.0)
            per[tid] = TaskResult(rewards=rewards, tokens=toks, missing=missing)
        return per, {"total_passes": passes, "n_trials": len(ids) * k}

    def load_trial(self, runs_dir, job, task_id, trial):
        p = Path(runs_dir) / "jobs" / job / f"{task_id}__{trial}.json"
        if not p.exists():
            return None
        rec = json.loads(p.read_text())
        rec["job"] = job
        return rec

    def render_trace(self, rec, detail=False):
        return "\n".join([
            f"TASK: {rec['task_id']} | trial: {rec['trial']} | job: {rec['job']}",
            f"REWARD: {rec['reward']} | level={rec['level']}",
            "=== TRAJECTORY ===",
            f"[step 1] AGENT: synthetic plumbing-only step for {rec['task_id']}",
            "[step 1] TERMINAL: synthetic output",
            "=== VERIFIER (ground truth) ===",
            f"synthetic reward {rec['reward']}",
        ])

    def task_row(self, task_id, rec, tr):
        return (f"{task_id} | rewards={tr.rewards if tr else '?'} | "
                f"level={rec.get('level')} | job={rec.get('job')}")

    def smoke(self, root, runs_dir, job, ids):
        with self._lock:
            self.smoke_calls.append({"job": job, "root": Path(root).name})
        for p in sorted(self.harness_dir(root).rglob("*.py")):
            try:
                compile(p.read_text(), str(p), "exec")
            except SyntaxError as e:
                return False, {"stage": "compile", "detail": f"{p.name}: {e.msg}"}
        self.run(root, runs_dir, job, ids, 1)
        found = len(list((Path(runs_dir) / "jobs" / job).glob("*__*.json")))
        if found < len(ids):
            return False, {"stage": "smoke_run", "missing_trials": f"{found}/{len(ids)}"}
        return True, {"stage": "smoke_run", "n": found}


# ------------------------------------------------------------- fake LLM ---
def _turn(text: str, marker: str | None = "=== INTERACTION LOG ===") -> int:
    """Number of completed agent turns in a prompt's interaction log."""
    if marker and marker in text:
        text = text.split(marker, 1)[1]
    return text.count("\n[you] ")


def _edit(eid, component, predicted, hypothesis):
    return {"id": eid, "component": component, "hypothesis": hypothesis,
            "targets_mode": "stub_level_too_low",
            "why_not_lower_lever": "plumbing-only scripted edit",
            "trigger_condition": "always (plumbing-only)",
            "predicted_affected": predicted,
            "retroactive_check": "plumbing-only scripted counterfactual",
            "regression_risk": "none (synthetic)"}


# (round t, variant, repair round?) -> one scripted action per proposer turn
PROPOSER_SCRIPTS = {
    (0, "A", False): [
        {"action": "list_files"},
        {"action": "read_file", "path": "agent.py"},
        {"action": "list_traces"},
        {"action": "read_trace", "task_id": "t2"},
        {"action": "edit_file", "path": "agent.py", "old": "LEVEL = 1", "new": "LEVEL = 4"},
        {"action": "done", "summary": "over-budget declaration (must be bounced)",
         "edits": [_edit("C1", "config", ["t2"], "plumbing-only"),
                   _edit("C2", "config", ["t3"], "plumbing-only"),
                   _edit("C3", "config", ["t4"], "plumbing-only")]},
        {"action": "done", "summary": "raise LEVEL to 4",
         "edits": [_edit("C1", "config", ["t2", "t3", "t4"],
                         "a higher LEVEL lets t2-t4 pass (synthetic)")]},
    ],
    (0, "B", False): [
        {"action": "read_file", "path": "agent.py"},
        {"action": "edit_file", "path": "agent.py", "old": "LEVEL = 1",
         "new": "LEVEL = 2  # PLANTED_LEAK_MARKER"},
        {"action": "done", "summary": "raise LEVEL to 2",
         "edits": [_edit("C1", "config", ["t2"], "LEVEL 2 lets t2 pass (synthetic)")]},
    ],
    (0, "B", True): [
        {"action": "edit_file", "path": "agent.py",
         "old": "LEVEL = 2  # PLANTED_LEAK_MARKER", "new": "LEVEL = 2"},
        {"action": "done", "summary": "raise LEVEL to 2 (leak removed)",
         "edits": [_edit("C1", "config", ["t2"], "LEVEL 2 lets t2 pass (synthetic)")]},
    ],
    (1, "A", False): [
        {"action": "read_file", "path": "agent.py"},
        {"action": "edit_file", "path": "agent.py", "old": "LEVEL = 4", "new": "LEVEL = = 5"},
        {"action": "done", "summary": "broken edit (must fail the smoke gate)",
         "edits": [_edit("C1", "config", ["t5"], "plumbing-only syntax error")]},
    ],
    (1, "B", False): [
        {"action": "edit_file", "path": "agent.py", "old": "LEVEL = 4", "new": "LEVEL = 3"},
        {"action": "done", "summary": "lower LEVEL to 3 (must fall below the floor)",
         "edits": [_edit("C1", "memory", ["t5"], "mislabelled on purpose (synthetic)")]},
    ],
}


class FakeLLM:
    """Scripted stand-in for rrsi.llm.generate, one entry point per role.
    Records every call so prompts can be checked after the run."""

    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def _log(self, role, prompt, system, json_only, model, cache_prefix, key, turn, act):
        reply = json.dumps(act)
        with self._lock:
            self.calls.append({"n": len(self.calls), "role": role, "model": model,
                               "json_only": json_only, "key": key, "turn": turn,
                               "prompt": prompt, "system": system or "",
                               "cache_prefix": cache_prefix or "", "reply": reply})
        return reply

    def count(self, role):
        return sum(1 for c in self.calls if c["role"] == role)

    def by(self, role, key=None, turn=None):
        return [c for c in self.calls if c["role"] == role
                and (key is None or c["key"] == key) and (turn is None or c["turn"] == turn)]

    def analyst(self, prompt, system=None, max_retries=6, json_only=False, model=None,
                max_tokens=None, cache_prefix=None):
        turn = _turn(prompt)
        if turn == 0:
            act = {"action": "digest_many", "requests": [
                {"task_id": "t6", "lens": "failure", "questions": ["what blocked the stub task?"]},
                {"task_id": "t1", "lens": "success"},
                {"task_id": "no-such-task", "lens": "failure"}]}
        else:
            act = {"action": "report",
                   "failure_modes": [{
                       "mode": "stub_level_too_low", "n_tasks": 2, "affected_tasks": ["t5", "t6"],
                       "description": "synthetic mode (plumbing-only)",
                       "needed_instead": "a higher LEVEL",
                       "representative_evidence": [
                           {"task_id": "t6", "where": "step 1", "quote": "synthetic"}]}],
                   "capability_gaps": [],
                   "success_habits": [{
                       "habit": "stub_habit", "n_tasks": 1,
                       "description": "synthetic habit (plumbing-only)",
                       "risk_if_broken": "none"}]}
        return self._log("analyst", prompt, system, json_only, model, cache_prefix, None, turn, act)

    def digester(self, prompt, system=None, max_retries=6, json_only=False, model=None,
                 max_tokens=None, cache_prefix=None):
        m = re.match(r"Assigned trace: (\S+)\.txt", prompt)
        tid = m.group(1) if m else "?"
        lm = re.search(r"Your lens for this assignment: (\w+)", system or "")
        lens = lm.group(1) if lm else "?"
        turn = _turn(prompt, marker=None)
        if turn == 0:
            act = {"action": "grep", "pattern": "REWARD", "path": f"{tid}.txt", "max_hits": 5}
        elif turn == 1:
            act = {"action": "bash", "cmd": f"grep -n REWARD {tid}.txt | head -3"}
        elif turn == 2:   # a write attempt the read-only shell must refuse
            act = {"action": "bash", "cmd": f"cat {tid}.txt > rrsi_plumbing_must_be_rejected.txt"}
        elif lens == "success":
            act = {"action": "return", "digest": {
                "habits": [{"habit": "stub habit", "where_shown": "step 1"}],
                "risk_if_removed": "none (synthetic)"}}
        else:
            act = {"action": "return", "digest": {
                "blocker": f"stub blocker for {tid}", "narrative": "synthetic (plumbing-only)",
                "evidence": [{"where": "line 2", "quote": "REWARD"}],
                "verifier_evidence": "synthetic reward 0.0",
                "needed_instead": "a higher LEVEL"}}
        return self._log("digester", prompt, system, json_only, model, cache_prefix,
                         (tid, lens), turn, act)

    def proposer(self, prompt, system=None, max_retries=6, json_only=False, model=None,
                 max_tokens=None, cache_prefix=None):
        m = re.search(r"You are variant ([A-H]) of round (\d+)\.", prompt)
        key = (int(m.group(2)), m.group(1), "REPAIR ROUND:" in prompt) if m else None
        turn = _turn(prompt)
        script = PROPOSER_SCRIPTS.get(key)
        if script is None or turn >= len(script):
            raise AssertionError(f"proposer script missing or exhausted: key={key} turn={turn}")
        return self._log("proposer", prompt, system, json_only, model, cache_prefix,
                         key, turn, script[turn])

    def critic(self, prompt, system=None, max_retries=6, json_only=False, model=None,
               max_tokens=None, cache_prefix=None):
        act = {"verdict": "accept", "reasons": [], "risk_notes": ["plumbing-only scripted verdict"]}
        return self._log("critic", prompt, system, json_only, model, cache_prefix, None, 0, act)


def _tripwire(*_a, **_k):
    raise AssertionError("the real rrsi.llm backend was reached in a plumbing-only test")


def _no_network(*_a, **_k):
    raise AssertionError("network access attempted in a plumbing-only test")


# --------------------------------------------------------------- helpers ---
def make_repo(repo: Path) -> Path:
    (repo / "domains" / "stub" / "harness").mkdir(parents=True)
    (repo / "domains" / "stub" / "SKILL.md").write_text(
        "# Stub constitution (plumbing-only)\nEdits must stay task-agnostic.\n")
    (repo / "domains" / "stub" / "PATTERNS.md").write_text(
        "# Stub pattern library (plumbing-only)\n")
    (repo / "domains" / "stub" / "harness" / "agent.py").write_text(H0_SOURCE)
    G.git(repo, "init", "-q", check=True)
    G.git(repo, "add", "-A", check=True)
    G.git(repo, "commit", "-q", "-m", "H0 stub harness (plumbing-only)", check=True)
    return repo


def make_cfg() -> RRSIConfig:
    return RRSIConfig(T=3, k=2, m=2, b_min=1, b_max=2, w=3, m_draft=1,
                      delta=0.05, delta_z=2.0, beta0=0.10, beta1=40.0,
                      w_s=0.0, w_c=15.0, w_n=0.5, n_prune=4, repair_rounds=2,
                      invalid_missing_frac=0.2, n_fail_traces=4, n_success_traces=2,
                      eval_parallel=1, proposer_model="stub-proposer-model",
                      analyst_model="stub-analyst-model", critic_model="stub-critic-model")


def git_out(repo, *args) -> str:
    return G.git(repo, *args, check=True).stdout.strip()


def full_sha(repo, ref) -> str:
    return git_out(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def read_json(p):
    return json.loads(Path(p).read_text())


def read_jsonl(p) -> list[dict]:
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def write_receipt(name, repo, runs_root, fake, domain, summary):
    """Copy the run's state out of the temporary directory (never raises)."""
    if not RECEIPT_ROOT:
        return
    try:
        out = Path(RECEIPT_ROOT) / name
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
        state = Path(runs_root) / "stub"
        if state.exists():
            shutil.copytree(state, out / "runs_state",
                            ignore=shutil.ignore_patterns("wt", "__pycache__"))
        if (Path(repo) / ".git").exists():
            refs = G.git(repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
            graph = G.git(repo, "log", "--all", "--graph", "--decorate",
                          "--format=%h %s").stdout
            (out / "git_refs_and_graph.txt").write_text(refs + "\n" + graph)
        with open(out / "llm_ledger.jsonl", "w") as f:
            for c in fake.calls:
                f.write(json.dumps({
                    **{k: c[k] for k in ("n", "role", "model", "json_only", "key", "turn", "reply")},
                    "prompt_chars": len(c["prompt"]),
                    "prompt_sha256": hashlib.sha256(c["prompt"].encode()).hexdigest()}) + "\n")
        with open(out / "llm_prompts.jsonl", "w") as f:
            for c in fake.calls:
                f.write(json.dumps({k: c[k] for k in ("n", "role", "system", "cache_prefix",
                                                      "prompt")}) + "\n")
        (out / "domain_calls.json").write_text(json.dumps(
            {"run_calls": domain.run_calls, "smoke_calls": domain.smoke_calls}, indent=1))
        (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    except Exception as e:  # noqa: BLE001 - a receipt problem must not mask the test result
        print(f"[receipt] WARNING could not write receipt {name}: {e!r}")


@pytest.fixture
def plumbing(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(llm_mod, "generate", _tripwire)
    monkeypatch.setattr(llm_mod, "_client_for", _tripwire)
    fake = FakeLLM()
    monkeypatch.setattr(analyst_mod, "generate", fake.analyst)
    monkeypatch.setattr(digester_mod, "generate", fake.digester)
    monkeypatch.setattr(propose_mod, "generate", fake.proposer)
    monkeypatch.setattr(critic_mod, "generate", fake.critic)
    repo = make_repo(tmp_path / "repo")
    return fake, StubDomain(), repo, tmp_path / "runs"


# ----------------------------------------------------------------- tests ---
def test_full_cycle_plumbing(plumbing):
    fake, domain, repo, runs_root = plumbing
    cfg = make_cfg()
    runs = runs_root / "stub"
    summary = {"test": "full_cycle", "frozen_base": FROZEN_BASE, "completed": False}
    try:
        h0 = full_sha(repo, "HEAD")

        # ---- baseline: Evaluate(H_0), frontier, calibration -------------------
        run = Run(domain, cfg, repo, runs_root)
        base = run.baseline("base")
        run.calibrate(["base"])
        assert base.S == pytest.approx(2 / 12) and base.C == pytest.approx(1000.0)
        fr = read_json(runs / "frontier.json")
        assert fr["incumbent"]["t"] == 0 and fr["incumbent"]["job"] == "base"
        assert full_sha(repo, fr["incumbent"]["commit"]) == h0 == full_sha(repo, "evolve/stub")
        assert fr["incumbent"]["harness_tree"] == \
            git_out(repo, "rev-parse", "evolve/stub:domains/stub/harness")[:12]
        assert fr["S_star"] == pytest.approx(2 / 12) and len(fr["trajectory"]) == 1
        assert [r["outcome"] for r in read_jsonl(runs / "history.jsonl")] == ["BASELINE"]
        cal = read_json(runs / "calibration.json")
        assert cal["jobs"] == ["base"] and cal["method"].startswith("bootstrap")

        # ---- round 0: two candidates, A wins ----------------------------------
        run.round(0)
        r0 = runs / "r0"
        rep = read_json(r0 / "analysis_report.json")
        assert rep["failure_modes"][0]["mode"] == "stub_level_too_low"
        assert rep["n_digests"] == 2
        assert (r0 / "analysis" / "digests" / "t6_failure.json").exists()
        assert (r0 / "analysis" / "digests" / "t1_success.json").exists()
        assert sorted(p.stem for p in (r0 / "analysis" / "rendered").glob("*.txt")) == TASKS
        assert not list((r0 / "analysis" / "rendered").glob("rrsi_plumbing_must_be_rejected*"))
        d0 = read_json(r0 / "directives.json")
        assert d0["b_t"] == edit_budget(0, cfg.T, cfg.b_min, cfg.b_max) == 2
        assert d0["sigma_t"] == 0 and d0["tried"] == [] and d0["delta"] == pytest.approx(0.05)

        prop_a = read_json(r0 / "A" / "proposal.json")      # over-budget done() bounced once
        assert prop_a["status"] == "done" and prop_a["n_edits"] == 1
        assert [x.get("action") for x in prop_a["log"]].count("done") == 2
        assert read_json(r0 / "A" / "critic.json")["verdict"] == "accept"
        assert read_json(r0 / "A" / "smoke.json")["ok"] is True
        prep_a = read_json(r0 / "A" / "prep.json")

        crit_b0 = read_json(r0 / "B" / "critic_a0.json")    # deterministic precheck, then repair
        assert crit_b0["verdict"] == "reject"
        assert crit_b0["reasons"] == ["precheck: planted leak marker in diff"]
        assert (r0 / "B" / "proposal_r1.json").exists()
        assert read_json(r0 / "B" / "critic_a1.json")["verdict"] == "accept"
        assert read_json(r0 / "B" / "critic.json")["verdict"] == "accept"
        assert "PLANTED_LEAK_MARKER" not in (r0 / "B" / "diff.patch").read_text()
        prep_b = read_json(r0 / "B" / "prep.json")

        evals = {c["job"]: c for c in domain.run_calls}    # each worktree measured on its own
        assert evals["r0A"]["level"] == 4 and evals["r0A"]["root"] == "r0A"
        assert evals["r0B"]["level"] == 2 and evals["r0B"]["root"] == "r0B"
        assert read_json(r0 / "A" / "eval.json")["S"] == pytest.approx(8 / 12)
        assert read_json(r0 / "B" / "eval.json")["S"] == pytest.approx(4 / 12)

        dec0 = {x["variant"]: x for x in read_json(r0 / "decisions.json")}
        assert dec0["A"]["admissible"] is True and dec0["B"]["admissible"] is True
        assert dec0["A"]["delta_S"] == pytest.approx(0.5)
        assert dec0["A"]["delta_C"] == pytest.approx(0.0)
        hist = read_jsonl(runs / "history.jsonl")
        rows0 = {r["variant"]: r for r in hist if r.get("t") == 0 and r.get("edit_id")}
        assert rows0["A"]["outcome"] == "ACCEPTED" and rows0["A"]["accepted"] is True
        assert rows0["A"]["component"] == "config" and rows0["A"]["bundle"] == 1
        assert rows0["B"]["outcome"] == "LOST" and rows0["B"]["accepted"] is False
        assert rows0["B"]["delta_S"] == pytest.approx(1 / 6, abs=1e-6)
        att = read_jsonl(runs / "attribution.jsonl")
        att_a = next(a for a in att if a["t"] == 0 and a["variant"] == "A")
        assert att_a["predicted_hit"] == ["t2", "t3", "t4"] and att_a["hit_rate"] == 1.0

        fr = read_json(runs / "frontier.json")               # H_1 = A, persisted
        a_sha = full_sha(repo, prep_a["commit"])
        assert full_sha(repo, "evolve/stub") == a_sha == full_sha(repo, fr["incumbent"]["commit"])
        assert fr["incumbent"]["t"] == 1 and fr["incumbent"]["job"] == "r0A"
        assert fr["incumbent"]["variant"] == "A"
        assert fr["incumbent"]["harness_tree"] == \
            git_out(repo, "rev-parse", f"{a_sha}:domains/stub/harness")[:12]
        assert fr["S_star"] == pytest.approx(8 / 12)
        assert [x["t"] for x in fr["trajectory"]] == [0, 1]
        assert "LEVEL = 4" in git_out(repo, "show", "evolve/stub:domains/stub/harness/agent.py")
        assert G.git(repo, "merge-base", "--is-ancestor", h0, "evolve/stub").returncode == 0
        assert G.branch_exists(repo, "stub/r0A") and G.branch_exists(repo, "stub/r0B")
        assert full_sha(repo, "stub/r0B") == full_sha(repo, prep_b["commit"])
        assert not (runs / "wt" / "r0A").exists() and not (runs / "wt" / "r0B").exists()

        b_read = fake.by("proposer", key=(0, "B", False), turn=1)[0]   # B drafted from H_0
        b_log = b_read["prompt"].split("=== INTERACTION LOG ===", 1)[1]
        assert "LEVEL = 1" in b_log and "LEVEL = 4" not in b_log

        # ---- round 1: smoke_fail + floor reject -> H_2 = H_1 ------------------
        run.round(1)
        r1 = runs / "r1"
        d1 = read_json(r1 / "directives.json")
        assert d1["b_t"] == edit_budget(1, cfg.T, cfg.b_min, cfg.b_max)
        assert d1["tried"] == ["config"] and d1["S_star"] == pytest.approx(8 / 12)
        prep_a1 = read_json(r1 / "A" / "prep.json")
        assert prep_a1["gate_failure"] == "smoke_fail" and '"compile"' in prep_a1["detail"]
        assert not G.branch_exists(repo, "stub/r1A")
        assert "r1A" not in {c["job"] for c in domain.run_calls}
        assert read_json(r1 / "B" / "eval.json")["S"] == pytest.approx(6 / 12)
        dec1 = {x["variant"]: x for x in read_json(r1 / "decisions.json")}
        assert dec1["A"]["admissible"] is False and dec1["A"]["reason"] == "smoke_fail"
        assert dec1["B"]["admissible"] is False
        assert "below noise-adjusted floor" in dec1["B"]["reason"]
        hist = read_jsonl(runs / "history.jsonl")
        rows1 = {r["variant"]: r for r in hist if r.get("t") == 1 and r.get("edit_id")}
        assert rows1["A"]["outcome"] == "smoke_fail" and rows1["A"]["delta_S"] is None
        assert rows1["B"]["outcome"] == "REJECTED"
        assert rows1["B"]["delta_S"] == pytest.approx(-1 / 6, abs=1e-6)
        assert read_json(r1 / "B" / "proposal.json")["edits"][0]["component"] == "memory"
        assert rows1["B"]["component"] == "config"           # re-tagged from the diff
        att = read_jsonl(runs / "attribution.jsonl")
        att_b1 = next(a for a in att if a["t"] == 1 and a["variant"] == "B")
        assert att_b1["predicted_hit"] == [] and att_b1["hit_rate"] == 0.0
        assert att_b1["unpredicted_regressions"] == ["t4"]

        fr = read_json(runs / "frontier.json")
        assert full_sha(repo, "evolve/stub") == a_sha
        assert fr["incumbent"]["t"] == 1 and full_sha(repo, fr["incumbent"]["commit"]) == a_sha
        assert [x["t"] for x in fr["trajectory"]] == [0, 1, 2]
        assert fr["trajectory"][2]["S"] == pytest.approx(8 / 12)
        assert fr["trajectory"][2]["job"] == "r0A"

        # round 1 consumed round 0's persisted state
        assert "level=4" in (r1 / "analysis" / "rendered" / "t5.txt").read_text()
        assert "stub_level_too_low" in fake.by("analyst")[2]["prompt"]
        p1 = fake.by("proposer", key=(1, "A", False), turn=0)[0]
        assert "===== FILE: agent.py =====\n" + H0_SOURCE.replace("LEVEL = 1", "LEVEL = 4") \
            in p1["prompt"]
        assert '"outcome": "ACCEPTED"' in p1["prompt"] and '"outcome": "LOST"' in p1["prompt"]
        assert '"predicted_hit": [' in p1["prompt"]

        # ---- every role ran, on the expected prompts, with no real backend ----
        assert {r: fake.count(r) for r in ("analyst", "digester", "proposer", "critic")} == \
            {"analyst": 4, "digester": 16, "proposer": 17, "critic": 4}
        assert {c["model"] for c in fake.by("proposer")} == {"stub-proposer-model"}
        assert {c["model"] for c in fake.by("analyst") + fake.by("digester")} == \
            {"stub-analyst-model"}
        assert {c["model"] for c in fake.by("critic")} == {"stub-critic-model"}
        assert all(c["json_only"] for c in fake.calls)
        for c in fake.by("proposer"):
            for section in ("=== CURRENT HARNESS SOURCE H_t ===", "=== EDIT HISTORY L_t",
                            "=== THIS ROUND'S EDIT BUDGET b_t ===",
                            "=== EXPLORATION DIRECTIVES E_t ===",
                            "=== COMPONENTS TO PRUNE B_t", "=== THREE-LENS ANALYSIS REPORT F_t",
                            "=== PER-TASK DIGESTS", "AT MOST 2 independent edit(s)"):
                assert section in c["prompt"], (c["key"], c["turn"], section)
            assert c["cache_prefix"].startswith("=== CONSTITUTION (SKILL.md) ===")
            assert "STUB-PROPOSER-BRIEF" in c["system"]
        repair = fake.by("proposer", key=(0, "B", True))
        assert repair and "precheck: planted leak marker in diff" in repair[0]["prompt"]
        bounce = fake.by("proposer", key=(0, "A", False), turn=6)[0]
        assert "exceed the budget b_t = 2" in bounce["prompt"]
        digs = fake.by("digester")
        assert all("STUB-DIGESTER-BRIEF" in c["system"] for c in digs)
        assert any("what blocked the stub task?" in c["system"] for c in digs)
        finals = fake.by("digester", turn=3)
        assert len(finals) == 4
        assert all("ERROR: command rejected" in c["prompt"] for c in finals)
        assert all("2:REWARD:" in c["prompt"] for c in finals)      # the read-only grep ran
        assert all("[1 requests skipped: unknown task_id]" in c["prompt"]
                   for c in fake.by("analyst", turn=1))
        crits = fake.by("critic")
        assert "=== DIFF ===" in crits[0]["prompt"] and "+LEVEL = 4" in crits[0]["prompt"]
        assert all("STUB-CRITIC-BRIEF" in c["system"] for c in crits)
        assert "anthropic" not in sys.modules

        summary.update({
            "h0_commit": h0, "incumbent_commit": a_sha,
            "S_trajectory": [x["S"] for x in fr["trajectory"]],
            "outcomes": [[r["t"], r["variant"], r["outcome"]] for r in hist],
            "llm_calls": {r: fake.count(r) for r in ("analyst", "digester", "proposer", "critic")},
            "domain_jobs": [c["job"] for c in domain.run_calls],
        })
        summary["completed"] = True
    finally:
        write_receipt("full_cycle", repo, runs_root, fake, domain, summary)


def test_round_resume_after_crash(plumbing):
    fake, domain, repo, runs_root = plumbing
    cfg = make_cfg()
    runs = runs_root / "stub"
    summary = {"test": "resume", "frozen_base": FROZEN_BASE, "completed": False}
    try:
        h0 = full_sha(repo, "HEAD")
        Run(domain, cfg, repo, runs_root).baseline("base")
        domain.crash_on_job = "r0B"
        with pytest.raises(SimulatedCrash):
            Run(domain, cfg, repo, runs_root).round(0)
        r0 = runs / "r0"
        assert (r0 / "A" / "eval.json").exists() and not (r0 / "B" / "eval.json").exists()
        assert (r0 / "B" / "prep.json").exists() and not (r0 / "decisions.json").exists()
        assert [r["outcome"] for r in read_jsonl(runs / "history.jsonl")] == ["BASELINE"]
        assert len(read_json(runs / "frontier.json")["trajectory"]) == 1
        assert full_sha(repo, "evolve/stub") == h0
        llm_before, jobs_before = len(fake.calls), len(domain.run_calls)

        Run(domain, cfg, repo, runs_root).round(0)          # as a restarted process would
        assert len(fake.calls) == llm_before                 # analysis, drafts, critic reused
        assert [c["job"] for c in domain.run_calls[jobs_before:]] == ["r0B"]
        dec = {x["variant"]: x for x in read_json(r0 / "decisions.json")}
        assert dec["A"]["S"] == pytest.approx(8 / 12) and dec["B"]["S"] == pytest.approx(4 / 12)
        rows = {r["variant"]: r["outcome"] for r in read_jsonl(runs / "history.jsonl")
                if r.get("t") == 0 and r.get("edit_id")}
        assert rows == {"A": "ACCEPTED", "B": "LOST"}
        fr = read_json(runs / "frontier.json")
        assert full_sha(repo, "evolve/stub") == full_sha(repo, read_json(r0 / "A" / "prep.json")["commit"])
        assert [x["t"] for x in fr["trajectory"]] == [0, 1] and fr["incumbent"]["job"] == "r0A"

        summary.update({"llm_calls_total": len(fake.calls),
                        "domain_jobs": [c["job"] for c in domain.run_calls],
                        "outcomes": rows})
        summary["completed"] = True
    finally:
        write_receipt("resume", repo, runs_root, fake, domain, summary)
