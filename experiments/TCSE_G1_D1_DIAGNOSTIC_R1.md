# TCSE G1 diagnostic D1 — isolated, disabled by default

Base: `tcse/provider-flex-2026-11-15` @ `448119e5115e7d1a95ae53cc6435f206074bf403`. Original `main` at frozen upstream commit is not modified. Previous 1.5B / 3B negative evidence remains unchanged.

**D1 adds no task-specific recovery logic.** Environment variable `TCSE_G1_PROGRESS_MODE` defaults to `off`. Allowed values: `off` (no metrics, no behavior change), `observe` (only count parser warnings/errors, repeated command-and-visible-error matches in metadata), `feedback` (observe plus a generic advisory *after* consecutive identical single commands yield identical visible error lines). It never rewrites, blocks, or silently replaces model actions. This is not a proved no-progress detector; terminal text does not establish filesystem-state identity. Polling and multi-command batches are deliberately excluded, and flags may still generate false positives.

Counterpart telemetry is written in `agent_result.metadata.tcse_progress_diagnostic`, including counts but no original terminal text, commands, or credentials. Existing native trajectory evidence and API request times continue to be recorded by RRSI. Any advisory is visible to the agent and must count as additional text/tokens in comparative evaluation.

D1 gate: run `python -m pytest tests`, `python -m compileall -q third_party/harbor_terminus2`, shell syntax check. D2 may only use safe synthetic prompts and should not run the two benchmark tasks. D3 paired comparisons require same model, context, time, and tasks with cost accounting; G2 stays blocked. No new model or Docker experiment is triggered by this D1 change.
