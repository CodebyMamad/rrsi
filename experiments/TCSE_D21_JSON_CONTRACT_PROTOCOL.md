# TCSE D2.1: output-format experiment (synthetic only)

Parent branch: tcse/g1-diagnostic-r1, SHA f20b53a985104603ff691fa7cdf70b57a4383d65.
The upstream main baseline, D1 observational diagnostics, and negative G1 receipts are preserved.

Aim: Determine if Ollama's structured generation can make Qwen2.5-Coder 1.5B
conform to the original Terminus2 JSON command format through LiteLLM.

Four prompts are imported verbatim from frozen D2. Modes: plain, JSON object,
JSON schema, interleaved in a predetermined schedule; temperature 0, max output
350 tokens, same model, same Ollama host/context and LiteLLM transport.
The schema allows 0-4 commands (to avoid forcing the exact-one-command outcome).
The independent measurements are: strict JSON, schema validity, acceptance by
the actual unmodified Terminus2 parser, exactly-one command, latency and whether
the previously failed synthetic command is repeated. Raw synthetic responses
are retained solely to diagnose format failures; no real tasks or shell
commands are ever executed, no credentials are supplied.

A green GitHub workflow means the preflight executed, NOT that the protocol
passed. D2.1 precheck passes only when the schema arm has all four prompts
schema- and parser-valid, exactly one command each, median latency under 35s,
no backend exceptions and no repeated known failed command.

Run budget: one hosted runner capped at 19 minutes and a model invocation
step capped at 13 minutes. This does not authorize G1 task evaluations or G2.
Do not merge to main without separate review. Structural compliance is not
the same as correct task reasoning.
