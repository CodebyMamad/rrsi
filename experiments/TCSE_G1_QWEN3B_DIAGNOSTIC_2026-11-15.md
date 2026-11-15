# TCSE G1 Qwen 3B — bounded diagnostic, 2026-11-15

## Baseline

Historical engineering smoke run 43749633702: Qwen2.5-Coder 1.5B via local Ollama, 12 maximum turns, both official Harbor tasks executed without infrastructure exceptions, rewards 0/2; each reached 12 turns; repeated JSON parser warnings. 11/11 offline unit tests passed.

## New diagnostic

Same RRSI fork branch, Terminus-2 harness, official extract-elf and fix-git tasks, original scoring. Changed to Qwen2.5-Coder 3B, 20 turns and explicit Ollama context length of 8192. Harbor pinned to previously observed version 0.26.0; Ollama image pinned to digest 910d0d81...24592. No cloud or paid model provider. Multiple factors differ; an observed improvement is not causal evidence for any one change.

Cost guardrails: one GitHub Actions job capped at 48 minutes, 40-minute trial subcommand timeout, two smoke tasks, one trial each. GitHub runner minutes and CPU are real resources. Do not run 89×2 G2 or evolutionary search.

After completion inspect uploaded artifact `tcse-g1-qwen3b-20turns` including per-task reward, episodes, exceptions, input/output token usage, parser warnings, trajectories, verifier output, environment receipts. If 0/2: diagnose parser compliance, command failures, context and truncation, turn exhaustion, model ability and task requirements before increasing model size. This is not original-paper RRSI reproducibility.
