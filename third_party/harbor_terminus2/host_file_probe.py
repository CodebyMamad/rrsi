"""D6 bounded host-owned filesystem postcondition observation.

Exact-content predicate on a single regular file directly under a trusted
directory file descriptor. The model cannot define predicates at runtime.
No arbitrary shell commands, path traversal, symlinks, or raw-content export.

Proof boundary: results describe a read/check interval, NOT an atomic
filesystem snapshot or evidence that an action caused the observed effect.
Host must control the root/predicate configuration and serialize conflicting
writers for a stable check; deliberate mutation races may remain undetectable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import errno
import os
import re
import stat
from pathlib import Path


class ProbeError(ValueError):
    pass


class Status(str, Enum):
    TRUE = "true_at_probe"
    FALSE = "false_at_probe"
    UNKNOWN = "indeterminate"


@dataclass(frozen=True)
class FilePredicate:
    identifier: str
    filename: str
    expected_sha256: str
    max_bytes: int = 1048576

    def __post_init__(self) -> None:
        if not self.identifier or not self.identifier.strip():
            raise ProbeError("Identifier required")
        name = self.filename
        if (not name or name in {".", ".."} or name != os.path.basename(name)
                or "/" in name or "\\" in name or "\x00" in name):
            raise ProbeError("Only one basename under the trusted root is admitted")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", self.expected_sha256):
            raise ProbeError("Trusted expected digest must be SHA-256 hex")
        if type(self.max_bytes) is not int or self.max_bytes <= 0 or self.max_bytes > 16777216:
            raise ProbeError("Read limit must be 1..16777216 bytes")


@dataclass(frozen=True)
class Sample:
    status: Status
    reason: str
    actual_sha256: str | None = None
    file_size: int | None = None


@dataclass(frozen=True)
class WindowReceipt:
    run_id: str
    action_id: str
    goal_id: str
    before: Status
    after: Status
    observed_false_to_true: bool
    external_intervention_recorded: bool
    action_causation_proven: bool = False
    task_solved_proven: bool = False


class HostFileProbe:
    """Root descriptor and goal registry are constructed outside agent control."""

    def __init__(self, trusted_root: str | Path, predicates: tuple[FilePredicate, ...]):
        if not predicates:
            raise ProbeError("At least one independently specified predicate is required")
        ids = [p.identifier for p in predicates]
        if len(set(ids)) != len(ids):
            raise ProbeError("Duplicate goal identifier")
        self._goals = {p.identifier: p for p in predicates}
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        self._root_fd = os.open(str(trusted_root), flags)

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _fingerprint(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    def sample(self, goal_id: str) -> Sample:
        if self._root_fd is None:
            raise ProbeError("Closed probe")
        if goal_id not in self._goals:
            raise ProbeError("Unregistered goal; no model-provided predicates")
        spec = self._goals[goal_id]
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(spec.filename, flags, dir_fd=self._root_fd)
        except FileNotFoundError:
            return Sample(Status.FALSE, "absent_at_check")
        except OSError:
            return Sample(Status.UNKNOWN, "unsafe_or_unreadable")
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                return Sample(Status.UNKNOWN, "non_regular_file")
            if before.st_size > spec.max_bytes:
                return Sample(Status.UNKNOWN, "over_read_limit")
            chunks = []
            size = 0
            while True:
                blob = os.read(fd, min(65536, spec.max_bytes + 1 - size))
                if not blob:
                    break
                chunks.append(blob)
                size += len(blob)
                if size > spec.max_bytes:
                    return Sample(Status.UNKNOWN, "over_read_limit")
            after = os.fstat(fd)
            try:
                named = os.stat(spec.filename, dir_fd=self._root_fd, follow_symlinks=False)
            except OSError:
                return Sample(Status.UNKNOWN, "path_replaced_during_check")
            if (self._fingerprint(before) != self._fingerprint(after)
                    or self._fingerprint(after) != self._fingerprint(named)
                    or not stat.S_ISREG(named.st_mode)):
                return Sample(Status.UNKNOWN, "file_changed_during_check")
            actual = sha256(b"".join(chunks)).hexdigest()
            return Sample(Status.TRUE if actual == spec.expected_sha256 else Status.FALSE,
                          "content_digest_checked", actual, size)
        except (OSError, ValueError):
            return Sample(Status.UNKNOWN, "read_unstable_or_failed")
        finally:
            os.close(fd)

    def check(self, goal) -> bool | None:
        """D5 ProbeAdapter interface; ONLY literal bool/None returned."""
        result = self.sample(goal.identifier)
        if result.status is Status.TRUE:
            return True
        if result.status is Status.FALSE:
            return False
        return None


class HostVerificationSession:
    """Pair fresh pre-/post-checks with a host-owned run/action identity.

    No claims of causality: another actor could have made the change.
    """

    def __init__(self, run_id: str, probe: HostFileProbe):
        if not run_id.strip():
            raise ProbeError("run identifier required")
        self.run_id = run_id
        self._probe = probe
        self._begun: dict[str, tuple[str, Sample, bool]] = {}
        self._finished: set[str] = set()

    def begin(self, action_id: str, goal_id: str, *, source_run_id: str) -> Sample:
        self._origin(source_run_id)
        if not action_id.strip() or action_id in self._begun:
            raise ProbeError("Action must be unique in this run")
        sample = self._probe.sample(goal_id)
        self._begun[action_id] = (goal_id, sample, False)
        return sample

    def note_intervention(self, action_id: str, *, source_run_id: str) -> None:
        self._origin(source_run_id)
        if action_id not in self._begun or action_id in self._finished:
            raise ProbeError("No open observation window")
        goal_id, before, _ = self._begun[action_id]
        self._begun[action_id] = (goal_id, before, True)

    def finish(self, action_id: str, *, source_run_id: str) -> WindowReceipt:
        self._origin(source_run_id)
        if action_id not in self._begun or action_id in self._finished:
            raise ProbeError("Missing/closed observation window")
        goal_id, before, intervention = self._begun[action_id]
        after = self._probe.sample(goal_id)
        self._finished.add(action_id)
        return WindowReceipt(
            run_id=self.run_id, action_id=action_id, goal_id=goal_id,
            before=before.status, after=after.status,
            observed_false_to_true=before.status is Status.FALSE and after.status is Status.TRUE,
            external_intervention_recorded=intervention,
        )

    def _origin(self, source_run_id: str) -> None:
        if source_run_id != self.run_id:
            raise ProbeError("Foreign or missing run provenance")
