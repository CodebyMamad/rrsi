# TCSE RRSI Provider-Flex (experimental branch)

Upstream pinned baseline: `be50316e1db05914068a973f322770ef08ed7ba1`. This fork branch changes **only model/provider plumbing**; task scoring, search and selection algorithms remain upstream. The main branch retains the original frozen baseline.

## Run locally or in Codespaces without Vertex

Install core: `python3 -m pip install -e '.[dev]'`; core tests: `python3 -m pytest -q tests`. For coding smoke, use genuine Docker and Harbor `>=0.18` in `domains/coding/.venv`. Docker must work for the current user. Set your own model service: no cloud is required.

Example: local Ollama (replace installed ID, first check `ollama list`):
```bash
export RRSI_POLICY_MODEL='ollama_chat/<installed-model>'
export RRSI_SEARCH_MODEL='ollama_chat/<installed-model>'
export RRSI_SEARCH_BACKEND='litellm'
export RRSI_POLICY_API_BASE='http://127.0.0.1:11434'
export RRSI_SEARCH_API_BASE='http://127.0.0.1:11434'
python3 rrsi.py --domain coding smoke
```
For search roles, `RRSI_PROPOSER_MODEL`, `RRSI_ANALYST_MODEL`, and `RRSI_CRITIC_MODEL` override the shared model. `RRSI_SEARCH_API_KEY` is optional when the provider requires it. For policy calls, standard LiteLLM provider env keys work; `RRSI_POLICY_API_KEY` can be used for an OpenAI-compatible endpoint.

OpenCode is an **agent client**, not a model itself. You can use OpenCode for development with a free model, or call a provider exposed via a compatible API from this RRSI branch. OpenCode's built-in free quota does **not** automatically become a general-purpose API key. Harbor also lists an `opencode` agent, but this branch intentionally retains the authentic Terminus-2 harness for research comparability.

For the original Vertex path: `pip install -e '.[dev,vertex]'`, set `RRSI_SEARCH_BACKEND=vertex` and the appropriate Vertex env. This is optional and not the default.

**Scientific statuses:** original G0 verified from a historical Colab receipt; this branch is not an authentic baseline reproduction. G1 remains unexecuted until real Docker/Harbor/model smoke receipts. Do not run G2 or recursive evolution before the smoke passes and cost is assessed. Never commit secret credentials.
