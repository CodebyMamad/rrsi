"""D7 restricted host-side action/observation coordinator.

This module runs OUTSIDE the agent container. The agent provides only its
Terminus JSON response. Run IDs, action IDs, goal predicate, exit receipts and
file probes are host-owned. No model statement certifies effects or task success.

Admitted execution class: one complete shell command per action in an isolated
Docker worker. Not a general Terminal-Bench / tmux replacement.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import subprocess
from typing import Protocol

from .action_contract import ActionContract
from .evidence_ledger import EvidenceLedger, Goal
from .host_file_probe import FilePredicate, HostFileProbe, HostVerificationSession, Status
from .terminus_json_plain_parser import TerminusJSONPlainParser


@dataclass(frozen=True)
class ExecutionReceipt:
    sent: bool | None  # None denotes transport ambiguity; never infer a submission
    terminal_text: str
    host_exit_code: int | None


class HostExecutor(Protocol):
    def execute(self, command: str) -> ExecutionReceipt: ...
    def quiesced(self): ...


@dataclass(frozen=True)
class ActionOutcome:
    run_id: str
    action_id: str
    disposition: str
    dispatch: str
    before_effect: str | None
    after_effect: str | None
    host_exit_code: int | None
    observed_false_to_true: bool
    external_intervention_recorded: bool
    action_caused_change_proven: bool = False
    task_solved_proven: bool = False
    refusal_reason: str | None = None


class _FrozenHostProbe:
    """Adapter for a single result of the already trusted D6 host sample.

    Prevents two inconsistent filesystem checks from being merged into one
    synthetic "verified" action. Never obtains evidence from model output.
    """
    def __init__(self, goal_id: str, status: Status):
        self.goal_id, self.status = goal_id, status

    def check(self, goal: Goal) -> bool | None:
        if goal.identifier != self.goal_id:
            raise ValueError("Host goal mismatch")
        if self.status is Status.TRUE:
            return True
        if self.status is Status.FALSE:
            return False
        return None


class HostActionCoordinator:
    """Host configuration cannot be changed by agent JSON or terminal output."""

    def __init__(self, *, run_id: str, goal: FilePredicate,
                 probe: HostFileProbe, executor: HostExecutor):
        if not run_id.strip():
            raise ValueError("run_id required")
        self.run_id = run_id
        self.goal = goal
        self.probe = probe
        self.executor = executor
        self.ledger = EvidenceLedger(run_id)
        self.session = HostVerificationSession(run_id, probe)
        self.contract = ActionContract("strict")
        self.parser = TerminusJSONPlainParser()

    def _refusal(self, action_id: str, reason: str) -> ActionOutcome:
        self.ledger.reject(action_id, source_run_id=self.run_id)
        return ActionOutcome(
            self.run_id, action_id, "rejected_without_dispatch", "rejected",
            None, None, None, False, False, refusal_reason=reason
        )

    def _unknown_dispatch(self, action_id: str, before_effect: str,
                          reason: str) -> ActionOutcome:
        self.ledger.unknown_dispatch(action_id, source_run_id=self.run_id)
        return ActionOutcome(
            self.run_id, action_id, "dispatch_unknown", "unknown",
            before_effect, None, None, False, False, refusal_reason=reason
        )

    def adjudicate(self, *, action_id: str, agent_response: str,
                   source_run_id: str) -> ActionOutcome:
        """Host calls this once per host-allocated action ID."""
        if source_run_id != self.run_id:
            raise ValueError("Cross-run attempt rejected before any dispatch")
        self.ledger.propose(action_id, Goal(self.goal.identifier))
        parsed = self.parser.parse_response(agent_response)
        if parsed.error:
            return self._refusal(action_id, "Malformed model response")
        if len(parsed.commands) != 1:
            return self._refusal(action_id, "Exactly one complete shell command admitted")
        cmd = parsed.commands[0].keystrokes
        if not cmd.strip():
            return self._refusal(action_id, "Empty command not admitted")
        issue = self.contract.validate([cmd])
        if issue:
            return self._refusal(action_id, issue)
        # The model's task_complete flag is deliberately ignored.
        pre = self.session.begin(action_id, self.goal.identifier,
                                 source_run_id=self.run_id)
        try:
            execution = self.executor.execute(cmd)
        except Exception:
            # An exception may arise AFTER a transport sent the command.
            return self._unknown_dispatch(action_id, pre.status.value,
                                          "Executor raised without a dispatch receipt")
        if execution.sent is None:
            return self._unknown_dispatch(action_id, pre.status.value,
                                          "Transport cannot establish dispatch")
        if execution.sent is False:
            return self._refusal(action_id, "Host executor explicitly reported no dispatch")
        self.ledger.submit(action_id, source_run_id=self.run_id)
        self.ledger.observe(action_id, execution.terminal_text, source_run_id=self.run_id)
        if type(execution.host_exit_code) is int:
            self.ledger.host_exit(action_id, execution.host_exit_code,
                                  source_run_id=self.run_id)
        # A live writer would invalidate the postcondition trust assumption.
        try:
            with self.executor.quiesced():
                receipt = self.session.finish(action_id, source_run_id=self.run_id)
                effect = self.ledger.check_effect(
                    action_id,
                    _FrozenHostProbe(self.goal.identifier, receipt.after),
                    source_run_id=self.run_id
                )
        except Exception:
            # Failed quiescence/probe MUST NOT become a fabricated false/true.
            return ActionOutcome(
                self.run_id, action_id, "observation_unavailable", "submitted",
                pre.status.value, "indeterminate", execution.host_exit_code,
                False, False, refusal_reason="Host isolation or probe failed"
            )
        # Only observation AT THE PROBE, no action causation or persistence.
        return ActionOutcome(
            self.run_id, action_id, "postcondition_observed", "submitted",
            receipt.before.value, effect.value, execution.host_exit_code,
            receipt.observed_false_to_true,
            receipt.external_intervention_recorded
        )


class DockerExecutor:
    """Host-owned Docker CLI transport; commands execute INSIDE container only.

    Security prerequisites: created externally by trusted host, no Docker
    socket mounted, no host policy directory mounted, no network, no privilege
    escalation, no uncontrolled external writers. Tests verify these options.
    """

    def __init__(self, container_name: str, *, timeout_seconds: int = 20):
        if not container_name or not all(c.isalnum() or c in "-_" for c in container_name):
            raise ValueError("Invalid host-selected container name")
        self.name = container_name
        self.timeout_seconds = timeout_seconds

    def execute(self, command: str) -> ExecutionReceipt:
        # An explicit host process receipt, NEVER based on terminal prose.
        # shell=False: command is passed as one argv element into the container.
        try:
            r = subprocess.run(
                ["docker", "exec", "--user", "65534:65534", self.name,
                 "sh", "-lc", command],
                capture_output=True, text=True, timeout=self.timeout_seconds,
                check=False,
            )
            if r.returncode in (125, 126, 127):
                # Reserved CLI/transport statuses overlap possible shell exits;
                # conservatively abstain rather than claim a process receipt.
                return ExecutionReceipt(None, (r.stdout + r.stderr)[-16000:], None)
            return ExecutionReceipt(True, (r.stdout + r.stderr)[-16000:], r.returncode)
        except subprocess.TimeoutExpired as exc:
            # May still have been submitted; no trusted exit receipt.
            parts = [p.decode("utf-8", "replace") if isinstance(p, bytes) else (p or "")
                     for p in (exc.stdout, exc.stderr)]
            return ExecutionReceipt(None, "".join(parts)[-16000:], None)

    @contextmanager
    def quiesced(self):
        # Fail closed on inability to pause. Resume even if probe raises.
        subprocess.run(["docker", "pause", self.name], check=True,
                       capture_output=True, text=True, timeout=20)
        try:
            yield
        finally:
            subprocess.run(["docker", "unpause", self.name], check=True,
                           capture_output=True, text=True, timeout=20)
