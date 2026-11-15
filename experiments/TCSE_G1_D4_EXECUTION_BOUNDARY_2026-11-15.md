# TCSE G1 — D4 execution-boundary gate, 2026-11-15

Source checkpoint: tcse/g1-diagnostic-r1 @ f20b53a985104603ff691fa7cdf70b57a4383d65.
Frozen original main, D2.1 schema work, D3 negative receipts, and D1 diagnostics are unchanged.

## New factual finding

Independent inspection of all four D3 terminal trajectories found that, in
feedback-mode fix-git, THREE of twelve model-generated shell keystrokes had
no final Enter/newline. The original TerminusJSONPlainParser accepted these
single commands (the existing no-newline warning applies only to nonfinal
commands in a batch). Tmux keystroke entry is not the same as execution.
In the observed trace an unterminated git checkout was concatenated with a
subsequent command. The agent then asserted success without supporting
execution evidence. The other three D3 trajectories had zero such keystrokes.

Other independent failures remained: hallucinated placeholder commit IDs,
repeated compilation/execution attempts, nonexistent files and nonsensical
username/password terminal commands. The D4 validator does NOT solve these.

## Admitted class and mechanism

Opt-in env TCSE_G1_ACTION_CONTRACT=off|strict, default off.
Strict is for **whole-command shell** episodes, not partial-line interactive
typing. Parse the model response with the original parser; BEFORE dispatch
to tmux, require each nonempty command to end in newline/carriage return
(or be one of C-c, C-d, C-m, Enter, KPEnter). For any invalid command,
reject the ENTIRE batch atomically and return an explicit parser-style
nonexecution error. No commands are silently rewritten and no task-specific
solutions supplied. No raw commands or tokens stored in additional metrics.

The gate prevents exactly one class: missing-Enter text erroneously treated
as a completed terminal action. It does not prove shell syntax correctness,
tool success, filesystem invariance, progress, or task completion.
A model may repeatedly submit invalid commands and exhaust its budget.
This implementation must not be marketed as mechanism discovery.

## Gate and resource budget

Run all baseline regression tests, new isolated tests, compileall, and
Harbor-backed _handle_llm_interaction tests. NO model/Harbor tasks, no G1
benchmark trials and no G2. Strict mode off is preserved as default.
A passing gate establishes code-integrity only. An independent,
budget-matched experiment would be needed before claiming agent improvement.

## Next research decision

This patch addresses real terminal action semantics with deterministic,
testable rejection. If offline tests pass, audit the other remaining
failure class: unsupported state/success claims despite explicit errors.
Do not repeat the previously falsified generic repetition-warning approach.
