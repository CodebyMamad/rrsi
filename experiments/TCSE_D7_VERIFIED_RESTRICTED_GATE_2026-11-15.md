# TCSE/RRSI — D7 host-worker gate: final restricted verdict (2026-11-15)

## Integrity and provenance

- Frozen upstream main: \`be50316e1db05914068a973f322770ef08ed7ba1\`, unchanged.
- Source branch: \`tcse/g1-d7-host-isolation-2026-11-15\`.
- Verified code/test commit: \`1d0d2926e50ccb0ca1c363d8a94e49a74a0cc00e\`.
- GitHub Actions run: https://github.com/CodebyMamad/rrsi/actions/runs/43769976517
- Artifact ID: \`14183986165\`, \`tcse-d7-host-boundary\`, compressed ZIP SHA-256
  \`c834a5009134d6098ad69632f2594d229c207838ff6d2cbc7cc230c9c960138b\`.
- GitHub Actions: **97/97 tests passed**, 1 existing nonblocking deprecation warning.
- Test worker image: \`busybox:1.36\` resolved image ID
  \`sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662\`.
- No model calls, no Terminal-Bench problems, no G1 or G2 trial.

## Verdict

\`D7_RESTRICTED_HOST_WORKER_GATE_PASSED\`
\`D7_GENERAL_HARBOR_DEPLOYMENT_NOT_ESTABLISHED\`

A separate, host-controlled coordinator can use the original Terminus JSON parser,
D4 Enter-boundary validation, D5 run/action evidence ledger, and D6 exact-file
postcondition probe while executing one shell command within an isolated Docker
worker. The host owns goal configuration and does not consult agent-provided
\`goal_id\` or agent-asserted completion to certify results.

The actual Docker worker's inspected constraints were:
network none; read-only rootfs; privileged=false; all Linux capabilities dropped;
no-new-privileges; exactly one writable workspace mount; Docker socket unmounted.
The host policy was not mounted in the worker. These observations substantiate
the restricted trust boundary *for this tested environment*; they are not a
general sandbox-escape security proof.

## Five actual host–worker receipts

1. Forged "SUCCESS", trusted exit 0, no file change -> **FALSE_AT_PROBE**.
2. Host sees correct content after a partial write and shell exit 7 ->
   **TRUE_AT_PROBE**; not evidence of task success or causation.
3. Agent supplies an unauthorized \`goal_id\`, target already satisfied ->
   host retains original predicate, TRUE both before and after;
   no new effect is claimed.
4. Command with no Enter boundary -> D4 rejects atomically; no Docker dispatch.
5. Command removes expected file -> TRUE before, FALSE after.

All five receipt events are preserved in the run artifact. D5 ledger recorded
22 events; the strict D4 boundary checked 5 commands and rejected one.

## Adversarial corrections during D7

- First coordinator incorrectly conflated a Docker transport exception with
  definitive non-dispatch. Revised D5 adds an explicit \`dispatch_unknown\`
  state. Docker CLI statuses 125–127 and transport timeouts conservatively
  abstain from exit-code certification, admitting possible false negatives.
- First coordinator used two postcondition reads (one in D6 and one in D5)
  that could disagree if the target changed between them. Now a host-owned
  immutable one-sample adapter passes the exact D6 sampled value into D5,
  during worker quiescence.
- Four new tests specifically cover these corrections. Last full CI:
  **97/97 passed**.

## Important unresolved production boundary

The host process in this test is separate from the **agent Docker worker**.
However, the evaluation mechanism has NOT been installed as a non-evolvable
host service in the **real Harbor G1 workflow**. The current RRSI coding
domain loads its Terminus harness from the candidate worktree. Putting the
same verifier inside that evolvable worktree would not establish an immutable
trust anchor against candidate modifications.

Further, the D6 exact-file predicate is a restricted example, NOT an
independent complete task oracle for the official \`extract-elf\` and \`fix-git\`
benchmark goals. Harbor's existing final task verifier remains the authority
for those rewards. Model-produced success claims remain untrusted.

These are precise *deployment and semantic* gaps, not missing unit tests.
Consequently **D7 cannot be declared production-complete** and D8 should
not be launched on the pretext that the restricted Docker proof is already
a genuine live-Harbor integration.

## Finite next decision

The original plan still has two formal gates:
1. **D7 (open production part):** protect the host evidence bridge outside
   the candidate-modifiable worktree, connect real Harbor action receipts
   without giving the model access to the task verifier, and demonstrate
   the trust boundary on an isolated Harbor-compatible task. Accept a
   documented BLOCK if the current Harbor architecture cannot enforce this.
2. **D8 (not started):** only if D7 production integration succeeds, run
   a bounded equal-model, equal-budget G1 comparison using independent Harbor
   rewards, full trajectories and runtime accounting; 2/2 or 0/2 alone
   cannot establish statistical superiority.

No additional numbered gate is proposed. G2 (178 trials) remains blocked.
