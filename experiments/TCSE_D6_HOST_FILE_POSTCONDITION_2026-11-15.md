# TCSE/RRSI D6 — Host-Owned Exact-File Postcondition Gate
2026-11-15

Parent checkpoint: D5.2 @ bbad4155d7d4664f6bf752373571ec4b266c10e2
Frozen upstream RRSI main remains unchanged. This is an offline verifier,
not a modification to the live agent or an authorization for G1/G2 tasks.

## Scope and trust model

One regular file directly inside a host-chosen directory. A host-owned
registry maps a predeclared Goal identifier to an exact basename and a trusted
SHA-256 digest of required bytes; the model cannot define or change that
predicate through the agent interface. The probe holds a directory fd, opens
using O_NOFOLLOW, bounds reads, checks regular-file type and compares
pre/post fstat metadata with the final pathname's stat. It returns:
TRUE only for stable exact digest, FALSE for stable mismatch or absence,
UNKNOWN for symlink, oversized, nonregular, unreadable, or detected mutation.
It stores hashes, no raw bytes, and has no arbitrary command execution.

A host-owned action observation window binds the run and action ID, records
fresh predicate checks before/after, rejects duplicate/stale receipts and
wrong-run attributions, and marks host-notified external intervention.
Observed false→true is NOT proof of causation: another writer could have
produced the postcondition, and the trust model does not authenticate actors.

## Adversarial conditions

Test missing/wrong/correct file, path traversal, symlink escape, preexisting
success, post-write process exit nonzero, false-to-true without causation,
stale cached state, duplicate receipts, foreign runs, invalid read,
oversized file, nonregular file, changed file during read, and pathname
replacement between open and final read. Verify D5 ProbeAdapter integration.

## Limitations and scope boundary

A file read is not an atomic snapshot. A malicious writer can mutate and
restore state between checks (ABA), alter the directory hierarchy above the
trusted descriptor, or change state immediately after the result. Host must
isolate/suspend conflicting writers for authoritative checks; this prototype
does not implement such isolation. Directory fd must be created by trusted
host code, not from user-supplied or model-supplied paths. FALSE for a missing
file applies to the observation interval, not forever. Sample timestamps and
event order do not imply causal identification. A digest equality proves
content only under the SHA-256 collision-resistance assumption.

No invented general-purpose verifier is claimed; this is a restricted
constructive mechanism for exact-file goals.

## Exit gate

D6 passes its **restricted** gate if all offline tests plus preserved D4/D5
tests pass and the evidence is versioned. It cannot certify benchmark
success, dynamic task semantics, or credible causal credit assignment.
D7 must connect a trusted verifier outside the agent sandbox, with enforced
quiescence/ownership before postcondition reports become authoritative.
D8 must then evaluate on matched G1 controls if D7 succeeds.
