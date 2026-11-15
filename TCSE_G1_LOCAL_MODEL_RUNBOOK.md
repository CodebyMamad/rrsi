# TCSE G1 — Docker/Harbor with a provider-neutral model

This branch has verified Python unit tests but has **not** reproduced the official policy (Claude Opus 4.8 on Vertex). The new workflow is a limited **engineering smoke** using a local Ollama model, not G2 and not a comparison against the upstream paper.

- Repository: `CodebyMamad/rrsi`, branch `tcse/provider-flex-2026-11-15`
- `G0`: historical original upstream commit `be50316e1db05914068a973f322770ef08ed7ba1` passed 8/8 in Colab.
- `CI`: 11/11 upstream+provider-flex tests passed in a separate GitHub Actions job.
- `G1`: two official tasks `extract-elf` and `fix-git`, one run each; this workflow uses `ollama_chat/qwen2.5-coder:1.5b` on CPU, solely to test liveness and wiring.
- **Cost ceiling**: one GitHub-hosted runner job of at most 35 minutes, plus 1GB-ish model download and two Harbor trials. No external paid model API or secrets.
- **Limit**: the model is much smaller than the paper's policy and can fail the tasks even when the system is wired correctly. A model failure is not scientific evidence of RRSI quality.
- GitHub Models cannot be used as a free shortcut: that product retired July 2026.

For local Linux/Codespaces Docker+Harbor with Ollama running on host:
```bash
git switch tcse/provider-flex-2026-11-15
python3 -m pip install -e '.[dev]'
python3 -m venv domains/coding/.venv
domains/coding/.venv/bin/pip install 'harbor>=0.18'
ollama pull qwen2.5-coder:1.5b
bash scripts/tcse_g1_local_smoke.sh
```
To use another LiteLLM-compatible provider, override `RRSI_POLICY_MODEL`, `RRSI_POLICY_API_BASE`, and any corresponding provider credentials **locally**; use a supported search model separately. See `TCSE_PROVIDER_FLEX.md`. Do not put credentials in Git history.

Evidence: download artifact `tcse-g1-local-model-smoke` from the workflow run. A successful G1 **requires** two actual result files, complete liveness, and no infrastructure/model exceptions; green GitHub unit tests alone do not satisfy G1. Do not begin the full 178-trial G2 baseline without a measured budget and a comparable model configuration.
