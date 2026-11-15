"""Optional observation-only or advisory G1 progress diagnostic.

No task-specific recipes, no command rewriting, no forced command blocking.
Records only counts / SHA-256 hashes; no raw terminal output or credentials.
Off by default. Does not establish that filesystem state is unchanged.
"""
from __future__ import annotations

import hashlib
import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FAILURE = re.compile(
    r"\b(error|fatal|failed|failure|not found|no such file|permission denied|"
    r"invalid|unknown command|cannot|could not|does not exist|not a git repository)\b",
    re.IGNORECASE,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _failure_fingerprint(output: str) -> str | None:
    """Hash exact visible error lines; avoid claiming semantic state equivalence."""
    lines = [_ANSI.sub("", line).strip() for line in output.splitlines()]
    lines = [" ".join(line.split()) for line in lines if _FAILURE.search(line)]
    return _sha("\n".join(lines)) if lines else None


class ProgressDiagnostic:
    """Per-run diagnostic, optional generic evidence-grounded feedback.

    A repeat requires one identical command in successive command-bearing
    episodes and an identical visible error signature. No state equivalence is
    inferred. All stored values are counters and digests rather than raw text.
    """

    MODES = ("off", "observe", "feedback")

    def __init__(self, mode: str = "off") -> None:
        if mode not in self.MODES:
            raise ValueError("TCSE_G1_PROGRESS_MODE must be off, observe, or feedback")
        self.mode = mode
        self.turns = 0
        self.parser_errors = 0
        self.parser_warnings = 0
        self.command_batches = 0
        self.single_command_error_observations = 0
        self.same_command_same_error_repeats = 0
        self.same_batch_same_error_repeats = 0
        self.feedback_events = 0
        self._previous: tuple[str, str] | None = None

    def record_parser(self, *, error: bool = False, warning: bool = False) -> None:
        if self.mode == "off":
            return
        self.parser_errors += int(bool(error))
        self.parser_warnings += int(bool(warning))
        if error:
            self._previous = None

    def observe(self, commands: list[str], terminal_output: str) -> str | None:
        """Detect exact consecutive single-command or whole-batch error repeats.

        Never attribute a batch-level error to any individual command.
        """
        if self.mode == "off":
            return None
        self.turns += 1
        self.command_batches += int(bool(commands))
        clean_commands = [c for c in commands if c.strip()]
        if not clean_commands:
            self._previous = None
            return None
        error_hash = _failure_fingerprint(terminal_output)
        if not error_hash:
            self._previous = None
            return None
        self.single_command_error_observations += 1
        current = (_sha("\\x00".join(clean_commands)), error_hash)
        repeated = self._previous == current
        self._previous = current
        if not repeated:
            return None
        if len(clean_commands) == 1:
            self.same_command_same_error_repeats += 1
        else:
            self.same_batch_same_error_repeats += 1
        if self.mode == "feedback":
            self.feedback_events += 1
            return (
                "PROGRESS DIAGNOSTIC (advisory): The immediately preceding "
                "terminal command batch matches the prior batch and "
                "the visible error lines match too. The signal does not identify "
                "which individual command caused the error. No progress is confirmed "
                "by this signal alone; check whether a different approach or "
                "new evidence is needed. Do not repeat solely because the "
                "previous attempt failed. You remain responsible for the next action."
            )
        return None

    def summary(self) -> dict[str, int | str]:
        return {
            "mode": self.mode,
            "turns": self.turns,
            "parser_errors": self.parser_errors,
            "parser_warnings": self.parser_warnings,
            "command_batches": self.command_batches,
            "single_command_error_observations": self.single_command_error_observations,
            "same_command_same_error_repeats": self.same_command_same_error_repeats,
            "same_batch_same_error_repeats": self.same_batch_same_error_repeats,
            "feedback_events": self.feedback_events,
        }
