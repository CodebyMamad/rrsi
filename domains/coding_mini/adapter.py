# SPDX-License-Identifier: Apache-2.0
# TCSE fork addition; not part of upstream RRSI.
"""TCSE P2-zero engineering-only domain.

The UNMODIFIED coding adapter (domains/coding/adapter.py: Harbor runner,
scoring, trace rendering, smoke gate, critic denylist, component signals,
role briefs) restricted to a two-task evolve set: the coding instance's own
official smoke tasks. Same starting harness H_0 (third_party/harbor_terminus2)
and the same constitution (domains/coding/SKILL.md and PATTERNS.md).
Hyperparameters live in ./rrsi.json.

Not a benchmark: two tasks, one trial each. It exists to exercise one real
round with real models; never report its score as a result.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CODING = Path(__file__).resolve().parent.parent / "coding" / "adapter.py"
_NAME = "domains.coding.adapter"

coding = sys.modules.get(_NAME)
if coding is None:
    _spec = importlib.util.spec_from_file_location(_NAME, _CODING)
    coding = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = coding
    _spec.loader.exec_module(coding)

MINI_TASKS = ["extract-elf", "fix-git"]


class CodingMiniDomain(coding.CodingDomain):
    name = "coding_mini"

    def evolve_ids(self) -> list[str]:
        return list(MINI_TASKS)

    def constitution(self, root: Path) -> tuple[str, str]:
        d = Path(root) / "domains" / "coding"
        return (d / "SKILL.md").read_text(), (d / "PATTERNS.md").read_text()


DOMAIN = CodingMiniDomain()
