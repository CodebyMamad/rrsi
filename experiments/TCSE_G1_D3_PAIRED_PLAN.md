# D3 restricted pilot, 2026-11-15

D1 offline gate 25/25 passed. D2 default LiteLLM protocol on the free 1.5B model yielded 0/4 strict JSON and 1/4 valid one-command response; p90 latency 10.221 seconds. D2B with identical four synthetic prompts and response_format=json_object yielded 4/4 strict JSON, 4/4 valid command, and p90 latency 8.504 seconds. Neither result claims task-level success.

The D3 development pilot uses the same two official G1 tasks, one trial each, and identical JSON-mode transport with pinned Ollama image and Qwen2.5-Coder 1.5B model in two conditions: (1) observe-only progress metadata, (2) feedback advisory plus the same metadata. Same nominal context 8192, max turns 12, Harbor 0.26.0 and per-task 900 s. A single job per condition, at most 27 minutes/job. Token cost is measured; advisory overhead is a confound, not a free benefit. Both models run on CPU hosted runners, no paid API, no Vertex.

Development evidence only: n=1 per task per condition is not statistical superiority. Outcomes must distinguish actual rewards, parser errors, execution exceptions, latency and repetitions. Preserve every negative finding and never overwrite the baseline snapshots. G2 and full self-evolution are explicitly out of scope. This workflow is not a reproduction of RRSI's original Vertex policy.
