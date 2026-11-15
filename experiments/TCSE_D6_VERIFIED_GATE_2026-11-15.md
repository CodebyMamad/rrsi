# TCSE/RRSI D6 — Independent restricted host postcondition gate

Date: 2026-11-15
Frozen upstream main: `be50316e1db05914068a973f322770ef08ed7ba1` (unchanged)
D5 parent checkpoint: `bbad4155d7d4664f6bf752373571ec4b266c10e2`
D6 verified source commit: `c11870e62066231191b7a48801ee12ac4613577c`
GitHub Actions run: https://github.com/CodebyMamad/rrsi/actions/runs/43769203375

## Gate

**D6_RESTRICTED_POSTCONDITION_GATE — PASSED**

- 80/80 total pytest tests passed; 22 new D6 tests and 58 inherited prior tests; one nonblocking deprecation warning.
- Original D4 dispatch gate and D5 evidence ledger regression tests passed in the same job.
- Python compileall and shell syntax check passed.
- Zero model calls, zero official benchmark tasks, no G2 run.
- Code and tests: `third_party/harbor_terminus2/host_file_probe.py`; `tests/test_g1_host_file_probe.py`.

## Constructively established

Host-configured exact SHA-256 content predicate for a single regular file beneath a trusted directory descriptor, bounded read size, symlink refusal, metadata change detection and final pathname identity verification.

The host may sample before/after an action and bind a result to an exact run/action ID. Invalid goals, duplicate receipts, cross-run sources and detected unstable reads fail closed. A D5 ProbeAdapter bridge is explicitly tested. Two worlds with identical terminal text and exit code but different file contents are distinguished.

## Negative boundaries preserved

This is a restricted exact-file observer, not a general semantic or causal verifier. Correct digest at observation time does NOT imply the action caused the change, that the property persists, or that a coding task passed. In-place adversarial ABA changes or writes immediately after the read cannot be ruled out. It assumes a protected host-controlled root and externally enforced quiescence against conflicting writes; no such scheduler or host-agent trust isolation is implemented by D6.

## Defined, bounded next gates

**D7 — Integration and host isolation.** Host owns root configuration and goal predicates outside the model sandbox; serialize a test action with fresh pre- and post-probes; bind provenance to the D5 record and D4 dispatch receipt; reject injected terminal 'SUCCESS' when the host predicate fails. Must demonstrate no model-specified predicate can become trusted and that unverified states remain indeterminate. If trusted isolation cannot be implemented, verdict BLOCK at D7 rather than add more prompt-based experiments.

**D8 — Controlled G1 validation.** Only after D7, evaluate on the two original official tasks with the same model and comparable time/turn/compute budgets; baseline versus guarded arm, independent Harbor verifier reward and cost/latency/trajectories. Improvement on two tasks is a smoke finding, not statistical superiority. If no improvement, record negative result and stop escalation.

**G2** remains separately gated; the full 178-trial paper baseline is not authorized by these results.

No additional numbered gates are implied by this roadmap.
