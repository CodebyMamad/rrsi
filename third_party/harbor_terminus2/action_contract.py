"""Optional execution-boundary contract for a tmux shell-command agent.

Strict mode prevents submitting nonempty terminal input that has no Enter key.
It does not validate shell syntax or whether any accepted command succeeds.
It is not suitable for workflows deliberately typing partial interactive lines.
"""
from __future__ import annotations
from dataclasses import dataclass

_CONTROLS = frozenset({"C-c", "C-d", "C-m", "Enter", "KPEnter"})

@dataclass
class ActionContract:
    mode: str = "off"
    checked_batches: int = 0
    checked_commands: int = 0
    rejected_batches: int = 0
    rejected_commands: int = 0

    def __post_init__(self) -> None:
        if self.mode not in {"off", "strict"}:
            raise ValueError("TCSE_G1_ACTION_CONTRACT must be off or strict")

    @staticmethod
    def _has_submission_boundary(value: str) -> bool:
        if not isinstance(value, str):
            return False
        if not value:
            return True
        if value in _CONTROLS:
            return True
        return value.endswith("\n") or value.endswith("\r")

    def validate(self, keystrokes: list[str]) -> str | None:
        """Fail the entire batch without terminal dispatch if one action is incomplete."""
        if self.mode == "off":
            return None
        self.checked_batches += 1
        self.checked_commands += len(keystrokes)
        invalid = [i + 1 for i, text in enumerate(keystrokes)
                   if not self._has_submission_boundary(text)]
        if not invalid:
            return None
        self.rejected_batches += 1
        self.rejected_commands += len(invalid)
        positions = ", ".join(str(i) for i in invalid)
        return (
            "TCSE_EXECUTION_CONTRACT_REJECTED: The command batch was NOT sent "
            f"to the terminal. Command position(s) {positions} contain nonempty "
            "keystrokes without a terminal Enter boundary (newline, carriage "
            "return or recognized control key). In this shell-command protocol, "
            "such text can remain pending and concatenate with later commands. "
            "Resubmit a complete command and wait for observed terminal evidence. "
            "This is NOT a claim about whether the proposed command would succeed."
        )

    def receipt(self) -> dict[str, int | str]:
        return {"mode": self.mode, "checked_batches": self.checked_batches,
                "checked_commands": self.checked_commands,
                "rejected_batches": self.rejected_batches,
                "rejected_commands": self.rejected_commands}
