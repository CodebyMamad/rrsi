# D2 synthetic probing, no benchmark

D1 original and diagnostic tests passed 25/25 in run 43759906962. This D2 probe uses the same **1.5B** model as the first liveness smoke, without using any real Terminal-Bench task. It sends four synthetic prompts via LiteLLM to local Ollama with a 180 s per-call timeout, and evaluates strict JSON and the authentic Terminus-2 parser separately. An explicit synthetic retry prompt checks whether the agent repeats an already failed dummy command.

Gate: 4/4 command-bearing parse-valid outputs, no repeated failed synthetic command, p90 latency <= 35 seconds. Failing this gate is a valid research result, not an infrastructure failure; it should prevent unplanned full task trials. The 35 s threshold is an engineering limit, not a theorem. This is not a controlled test of the optional progress-guard intervention (which remains disabled), and no original RRSI reproduction or G2 is authorized by this diagnostic.

Only four API requests to local Ollama; no paid API, no Vertex, no secrets. GitHub runner 18-minute cap and 900-second subprocess bound. Output in GitHub Actions artifact, without prompt/response plaintext; SHA256 hashes are retained. The original 1.5B/3B task failure receipts remain frozen.
