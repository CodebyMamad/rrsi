# D2B — synthetic JSON-mode test

The D2 result on run 43760298933 was STOP: 0/4 strict JSON, 1/4 exactly-one-command parser successes, p90 10.221s and no synthetic repeated failed command. This is evidence of protocol noncompliance despite acceptable small-model latency.

D2B repeats **the identical four synthetic prompts** with the same local Qwen2.5-Coder 1.5B model and adds only the `response_format={"type":"json_object"}` parameter through LiteLLM. The upstream parser is not altered, no real benchmark task is used, no benchmark solution is revealed, and no API key or Vertex is required. Compare strict-json and valid-one-command rates; the intended outcome is to identify a protocol lever, not claim task-level improvement. Strict 4/4, parsed-one-command 4/4, no repeated dummy command, p90 <= 35 seconds are the preregistered engineering gates. Single-sample stochasticity prevents statistical superiority claims.

If this fails, keep original RRSI algorithm unchanged; inspect schema, parser and transport, then re-evaluate a different provider or model on synthetic prompts only. Do not automatically start any task-level D3 experiment or G2.
