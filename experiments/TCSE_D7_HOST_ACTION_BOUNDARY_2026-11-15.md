# TCSE D7 — Host-side command/effect integration, restricted envelope

Frozen main and historical negative G1 results remain unchanged.

## Admitted interface

A *host-side* coordinator uses the ORIGINAL TerminusJSONPlainParser,
the opt-in D4 strict Enter-boundary checker, the D5 evidence ledger with
run/action binding, and the D6 exact-file host postcondition probe.
It accepts exactly ONE complete shell command per action. A host-owned
Docker executor submits the command via argv-form docker exec (no host shell).
No model-provided goal_id, expected hash, run ID, or "SUCCESS" text can
promote an unchecked observation to trusted evidence.

The executor pauses the entire isolated worker before both D6 host samples
(after execution and ledger check) and resumes afterward. If pause fails
the effect remains UNCHECKED. Nonzero exit may coexist with a true
postcondition; zero exit and optimistic prose can coexist with false.

## Trust model

The trusted host script configures expected digest, bind-mounted workdir,
and Docker flags. The agent container is nonprivileged, no network,
read-only rootfs, no Docker socket, and only a designated writable
workdir mounted. Test checks should verify these properties and
demonstrate that the worker cannot read host policy files.
The agent is not allowed to choose predicates.

This is an isolated **host-owned evaluation path**, not yet a replacement
for Harbor's live tmux harness: the current RRSI candidate worktree is
evolvable code, so importing a verifier there alone does not prove
integrity against an adversarial candidate. Do NOT wire it into the
agent's editable code and then call it trusted. Deployment must preserve
the host/worker privilege boundary for every trial.

## Nonclaims

No general task verifiers; no causal credit; no assured filesystem
snapshot against arbitrary privileged host writers; no infinite-time
durability; no benchmark performance; no novel universal mechanism.

A green unit-test job is an implementation check only. A separate
container isolation job must confirm executable boundary properties.
D7 full passage requires those properties and a host receipt invariant;
otherwise verdict RESTRICTED_ONLY or BLOCK. D8 may begin only if the
same isolation is preserved in the intended G1 evaluation path.
