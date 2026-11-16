# TCSE D7 production Harbor integration — verified STOP gate (2026-11-15)

## Verdict: D7_PRODUCTION_BLOCKED_BY_SHARED_VERIFIER

This is a concrete *negative* scientific/engineering finding. It is not
a claim that Harbor lacks independent verifiers in general, that task rewards
are invalid, or that the Docker host-worker mechanism from D7 failed.

The current unmodified Terminal-Bench 2.1 tasks used by G1 do not establish
the required separation. Historical **native Harbor trial result.json**
receipts from the real G1 runs explicitly record
\`verifier_environment_mode = "shared"\` for BOTH
\`terminal-bench/extract-elf\` and \`terminal-bench/fix-git\`.
The source dataset digest refs are respectively:

- \`sha256:1ef31d566be4fe3459d5368621ae7ef7a31b23ef675737e473bbc43c8c7b3fce\`
- \`sha256:16948b980df9d96de616a205f5acca1c5d395de83ff4f8ffabcafacb93226f2e\`

Both observed source task configs in \`harbor-framework/terminal-bench-2-1\`
declare a standard [verifier] block without separate environment mode or
a dedicated verifier image. Their tests run inside the shared environment.

Harbor v0.26.0 does support a separate verifier environment; this is
not a general Harbor impossibility. However imposing that mode would
require a verified, task-compatible conversion. Doing so on only
our branch would change the evaluation condition and could make a
non-comparable "original benchmark" result. The agent's Python
Terminus/tmux implementation is also loaded from an evolvable candidate
worktree, not from an immutable host boundary. Even switching to a
separate verifier would NOT automatically supply trusted per-action
execution receipts.

## New safeguard

\`scripts/tcse_harbor_trust_gate.py\` is a standard-library host-side
post-run check. It parses native Harbor result files directly or reads
existing artifact ZIP members without extracting. It checks exact
task identity/digest and recorded verifier_environment_mode.
If any original task uses shared mode, it returns
\`D7_BLOCKED_SHARED_VERIFIER\`. If all are separate but no independent
action receipts are present, it reports
\`D7_HOST_ACTION_RECEIPTS_MISSING\`; it NEVER authorizes D8 based on
this limited evidence. Unexpected/missing task IDs or malformed
records produce \`D7_INSUFFICIENT_OR_CHANGED_EVIDENCE\`.

The offline workflow explicitly expects the historical BLOCK verdict;
GitHub green means the guard is correctly detecting the blocked state,
NOT that D7 production integration is complete.

## Evidence provenance

All fixtures are non-secret summaries of actual earlier \`result.json\`
fields and SHA-256 digests. The original unchanged ZIP receipts remain
linked through existing GitHub Actions artifacts and previous preserved
checkpoint packages. No hidden verifier tests, task solutions, or
rewards are made visible to an agent.

Source packages:
- GitHub D3: https://github.com/CodebyMamad/rrsi/actions/runs/43761001377
- GitHub 3B G1: https://github.com/CodebyMamad/rrsi/actions/runs/43751293319

Local SHA-256 of the original D3 receive ZIPs:
- observe \`a14f2e77fa7ccf81e236ae8324f2a61347c1c8f9d232f483bf4c9541d53ad15b\`
- feedback \`9e4b3be52a88064df114bc6f9ae517626272418de17c64f24b161efb1c5b49a5\`

## What remains to unblock without inventing new stages

1. Task-compatible isolated verifier outside the mutable agent
   environment must be made authoritative by benchmark/Harbor, or an
   independently maintained trusted external verification procedure
   must be developed and rigorously reconciled with unchanged tasks.
2. Host-per-action receipts must be enforced outside the candidate's
   writable worktree and confirmed on the true Harbor execution path.
3. Only then can D7 pass production. D8 remains BLOCKED and unrun.

This is a STOP decision on the current attempted integration. No new
model requests, G1 task runs, or G2 trials were executed.

Main remains frozen at \`be50316e1db05914068a973f322770ef08ed7ba1\`.
