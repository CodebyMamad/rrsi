# TCSE/RRSI D5 — Action–Effect Evidence Boundary

**Date:** 2026-11-15. Parent branch: tcse/g1-action-contract-2026-11-15
at 7b33ccbe52d0c37b3c893bf8817123b570cce43f.
The original RRSI main, previous negative D3 results, and D4 repair are preserved.
No model, Harbor benchmark, G1 or G2 runs are authorized by this checkpoint.

## Negative theorem: observationally equivalent worlds

Let c be a submitted command, o terminal text, e a trusted zero exit
status, and P(s_after) its intended state predicate. Two deterministic
worlds are possible:

- W1 executes c, emits o="Done\n" and exit e=0, and sets P to true.
- W2 executes the SAME c, emits the SAME o and e=0, but leaves P false.

The observable triple (c,o,e) is identical in both worlds. Every rule
F(c,o,e) yields the same classification. Therefore no binary rule
using only this observation can be both sound and complete on both
worlds. Under an equal prior, a randomized forced binary guess cannot
exceed 1/2 average accuracy. A sound detector can abstain.

Even independent postcondition observation P=true does NOT show that c
caused P: P might already have held before c. Causal credit needs
pre-action evidence and noninterference or stronger interventions.

## Second obstruction: partial effects despite failure

A process may write an artifact then exit nonzero. A single chain
PROPOSED->SUBMITTED->OBSERVED->FAILED/VERIFIED is therefore the wrong
type: process failure and a verified postcondition may coexist. D5
represents transport, terminal observation, host process exit, and
independently checked effect as separate dimensions.

## Implemented constructive restriction

Pure host-side Python EvidenceLedger:
- Propose uniquely identified actions against a declared Goal predicate.
- Reject before dispatch, or submit keystrokes without inferring execution.
- Associate untrusted terminal text with the correct submitted action;
  store only output SHA-256 in the ledger.
- Separately record trusted-host integer exit codes; neither "exit 0" in
  text nor an exit status of 0 establishes the desired environmental effect.
- An independent read-only host ProbeAdapter can check the predeclared
  predicate. Only literal bool True establishes TRUE_AT_PROBE.
  False is FALSE_AT_PROBE; exceptions/nonboolean are INDETERMINATE.
  Missing adapter is rejected. Later probes overwrite earlier status,
  with probe-event identity retained.
- Causal change and task success are NEVER proven by this ledger.

## Crucial trust boundary and limitations

The adapter is assumed to be a trusted host instrument. The library
does not cryptographically authenticate callback implementations,
sandbox adversarial code, infer goals from language, prove execution
from tmux display, or supply a generic environment predicate checker.
This prototype is NOT wired to the agent; such integration would risk
promoting untrusted terminal text to verified evidence. A true-at-probe
result is time-local, not an invariant or proof of causation.

## Gate criteria and decision

Offline tests MUST exhibit two indistinguishable transcripts with
opposite effects; prove zero exit is insufficient; exhibit nonzero
exit plus verified partial effect; prevent evidence on rejected actions;
reject cross-action observations; refuse agent-asserted success;
fail closed on missing/broken/invalid probes; distinguish preexisting
state from causation; and preserve all D4 regression tests.
No model or coding benchmark tasks run.

If offline CI passes, verdict:
D5_RESTRICTED_LEDGER_CORRECTNESS / NO_TASK_COMPETENCE_EVIDENCE.
Next frontier is a trusted, goal-specific observation adapter in
a separately instrumented isolated environment. If it cannot be
provided, abstain rather than run further prompt-only evaluations.


## D5.1 adversarial correction — colliding local action IDs

The first D5 draft allowed the same local action ID to occur in two
different runs and did not require the evidence-producing caller to name
the run. A concrete replay accidentally routed a run-B observation to
run-A under the same ID; the first draft accepted it.

D5.1 repairs this interface: EVERY rejection, dispatch, observation,
host-exit and postcondition check requires a source_run_id that must
exactly equal the ledger run ID. Missing/mismatched origins fail closed.
Two additional tests reproduce and reject the old collision. This is
an honest-host consistency constraint, not cryptographic authentication:
a malicious producer could lie about its source_run_id. Real harness
integration therefore still requires a trusted receipt producer.
The revised offline D5 test count is 18.
