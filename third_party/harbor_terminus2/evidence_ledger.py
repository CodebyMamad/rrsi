"""D5 conservative host-side action/effect evidence ledger.

Terminal text, submitted keystrokes, host exit codes and independently checked
postconditions are DISTINCT evidence. The ledger never certifies causation,
persistent truth, or task completion.

Assumption: ProbeAdapter is supplied by an independently trusted, read-only
host. This pure-Python library does not authenticate arbitrary caller code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Protocol


class EvidenceError(ValueError):
    """Invalid state transition or missing proof prerequisite."""


class Dispatch(str, Enum):
    PROPOSED = "proposed"
    REJECTED = "rejected"
    SUBMITTED = "submitted"  # Keystrokes sent, not proof of execution.


class Effect(str, Enum):
    UNCHECKED = "unchecked"
    TRUE_AT_PROBE = "true_at_probe"
    FALSE_AT_PROBE = "false_at_probe"
    PROBE_INDETERMINATE = "probe_indeterminate"


@dataclass(frozen=True)
class Goal:
    identifier: str

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise EvidenceError("Goal identifier cannot be empty")


class ProbeAdapter(Protocol):
    def check(self, goal: Goal) -> bool:
        """Host-trusted read-only check of a predeclared predicate."""


@dataclass(frozen=True)
class Event:
    sequence: int
    action_id: str
    type: str
    evidence_digest: str | None = None


@dataclass
class Record:
    action_id: str
    goal: Goal
    dispatch: Dispatch = Dispatch.PROPOSED
    terminal_observed: bool = False
    terminal_digest: str | None = None
    host_exit_status: int | None = None
    effect: Effect = Effect.UNCHECKED
    effect_probe_sequence: int | None = None

    def snapshot(self) -> dict[str, str | int | bool | None]:
        """Effect holds only at last probe, not necessarily now or causally."""
        return {
            "action_id": self.action_id,
            "goal": self.goal.identifier,
            "dispatch": self.dispatch.value,
            "terminal_observed": self.terminal_observed,
            "host_exit_status": self.host_exit_status,
            "process_exited_nonzero": (
                None if self.host_exit_status is None else self.host_exit_status != 0
            ),
            "effect": self.effect.value,
            "effect_probe_sequence": self.effect_probe_sequence,
            "action_caused_change_proven": False,
            "task_solved_proven": False,
        }


@dataclass
class EvidenceLedger:
    """Append-only host evidence events, without inferring effects from text."""
    run_id: str
    _records: dict[str, Record] = field(default_factory=dict, init=False, repr=False)
    _events: list[Event] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise EvidenceError("run_id cannot be empty")

    def _event(self, action_id: str, kind: str, digest: str | None = None) -> None:
        self._events.append(Event(len(self._events) + 1, action_id, kind, digest))

    def _require_origin(self, source_run_id: str) -> None:
        if source_run_id != self.run_id:
            raise EvidenceError("Receipt source run does not match ledger run")

    def _record(self, action_id: str) -> Record:
        try:
            return self._records[action_id]
        except KeyError as exc:
            raise EvidenceError("Unknown action in this run") from exc

    def propose(self, action_id: str, goal: Goal) -> None:
        if not action_id.strip() or action_id in self._records:
            raise EvidenceError("Action identifier must be nonempty and unique in a run")
        if not isinstance(goal, Goal):
            raise EvidenceError("A goal predicate must be declared before dispatch")
        self._records[action_id] = Record(action_id=action_id, goal=goal)
        self._event(action_id, "proposed")

    def reject(self, action_id: str, *, source_run_id: str) -> None:
        self._require_origin(source_run_id)
        r = self._record(action_id)
        if r.dispatch != Dispatch.PROPOSED:
            raise EvidenceError("Only proposed actions can be rejected")
        r.dispatch = Dispatch.REJECTED
        self._event(action_id, "rejected_before_dispatch")

    def submit(self, action_id: str, *, source_run_id: str) -> None:
        """Host reports sent keystrokes; this DOES NOT prove execution/success."""
        self._require_origin(source_run_id)
        r = self._record(action_id)
        if r.dispatch != Dispatch.PROPOSED:
            raise EvidenceError("Only proposed actions can be dispatched once")
        r.dispatch = Dispatch.SUBMITTED
        self._event(action_id, "submitted_no_execution_certification")

    def observe(self, action_id: str, terminal_text: str, *, source_run_id: str) -> None:
        """Untrusted terminal text, including 'success', cannot prove effects."""
        self._require_origin(source_run_id)
        r = self._record(action_id)
        if r.dispatch != Dispatch.SUBMITTED:
            raise EvidenceError("No observation can be linked to an undispatched action")
        if r.terminal_observed:
            raise EvidenceError("This minimal one-observation action record is closed")
        if not isinstance(terminal_text, str):
            raise EvidenceError("Terminal observation must be text")
        digest = sha256(terminal_text.encode("utf-8", errors="replace")).hexdigest()
        r.terminal_observed = True
        r.terminal_digest = digest
        self._event(action_id, "untrusted_terminal_observed", digest)

    def host_exit(self, action_id: str, status: int, *, source_run_id: str) -> None:
        """Trusted host receipt only. Nonzero does not exclude partial effects."""
        self._require_origin(source_run_id)
        r = self._record(action_id)
        if r.dispatch != Dispatch.SUBMITTED or not r.terminal_observed:
            raise EvidenceError("Exit receipt requires dispatched and observed action")
        if type(status) is not int:
            raise EvidenceError("Host exit receipt requires integer (not bool or text)")
        if r.host_exit_status is not None:
            raise EvidenceError("Exit receipt already recorded")
        r.host_exit_status = status
        self._event(action_id, "host_exit_receipt",
                    sha256(str(status).encode()).hexdigest())

    def check_effect(self, action_id: str, adapter: ProbeAdapter, *, source_run_id: str) -> Effect:
        """Check goal at probe time, never infer causal change or persistence."""
        self._require_origin(source_run_id)
        r = self._record(action_id)
        if r.dispatch != Dispatch.SUBMITTED or not r.terminal_observed:
            raise EvidenceError("Effect check requires submitted and observed action")
        if adapter is None or not callable(getattr(adapter, "check", None)):
            raise EvidenceError("A trusted host probe adapter is mandatory")
        try:
            verdict = adapter.check(r.goal)
        except Exception:
            verdict = None
        if verdict is True:
            r.effect = Effect.TRUE_AT_PROBE
        elif verdict is False:
            r.effect = Effect.FALSE_AT_PROBE
        else:
            r.effect = Effect.PROBE_INDETERMINATE
        self._event(action_id, "trusted_postcondition_probe_" + r.effect.value)
        r.effect_probe_sequence = len(self._events)
        return r.effect

    def snapshot(self, action_id: str) -> dict[str, str | int | bool | None]:
        return self._record(action_id).snapshot().copy()

    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)
