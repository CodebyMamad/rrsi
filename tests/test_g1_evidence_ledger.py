"""D5 offline mathematical counterexample and evidence transition tests."""
import importlib.util
from pathlib import Path
import sys
import pytest

FILE = Path(__file__).resolve().parents[1] / "third_party" / "harbor_terminus2" / "evidence_ledger.py"
SPEC = importlib.util.spec_from_file_location("tcse_d5_ledger_test_module", FILE)
MOD = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOD
SPEC.loader.exec_module(MOD)
Ledger, Goal, Effect, Error = MOD.EvidenceLedger, MOD.Goal, MOD.Effect, MOD.EvidenceError


class MockTrustedWorld:
    def __init__(self, has_effect):
        self.has_effect = has_effect

    def check(self, goal):
        assert goal.identifier == "target-file-changed"
        return self.has_effect


def ledger(run="trace-1", action="a1"):
    x = Ledger(run)
    x.propose(action, Goal("target-file-changed"))
    return x


def test_indistinguishable_worlds_need_independent_probe():
    a, b = ledger("world-a"), ledger("world-b")
    for x in (a, b):
        x.submit("a1")
        x.observe("a1", "Done\n")
        x.host_exit("a1", 0)
        assert x.snapshot("a1")["effect"] == "unchecked"
    assert a._record("a1").terminal_digest == b._record("a1").terminal_digest
    assert a.check_effect("a1", MockTrustedWorld(True)) == Effect.TRUE_AT_PROBE
    assert b.check_effect("a1", MockTrustedWorld(False)) == Effect.FALSE_AT_PROBE
    assert not a.snapshot("a1")["action_caused_change_proven"]


def test_process_nonzero_can_coexist_with_effect_true():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Error after partial write\n")
    a.host_exit("a1", 1)
    assert a.check_effect("a1", MockTrustedWorld(True)) == Effect.TRUE_AT_PROBE
    x = a.snapshot("a1")
    assert x["process_exited_nonzero"] is True
    assert x["effect"] == "true_at_probe"


def test_untrusted_success_message_cannot_verify():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "SUCCESS. Task fixed. Everything verified!\n")
    assert a.snapshot("a1")["effect"] == "unchecked"
    assert a.snapshot("a1")["task_solved_proven"] is False


def test_rejected_action_cannot_be_submitted_or_observed_or_verified():
    a = ledger()
    a.reject("a1")
    with pytest.raises(Error): a.submit("a1")
    with pytest.raises(Error): a.observe("a1", "Done")
    with pytest.raises(Error): a.check_effect("a1", MockTrustedWorld(True))
    assert a.snapshot("a1")["dispatch"] == "rejected"


def test_no_cross_run_or_unknown_action_attribution():
    a = ledger("run-a", "run-a-item")
    a.submit("run-a-item")
    with pytest.raises(Error): a.observe("run-b-item", "Done")


def test_no_effect_probe_before_observation():
    a = ledger()
    with pytest.raises(Error): a.check_effect("a1", MockTrustedWorld(True))
    a.submit("a1")
    with pytest.raises(Error): a.check_effect("a1", MockTrustedWorld(True))


def test_missing_adapter_fails_closed():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Done")
    with pytest.raises(Error): a.check_effect("a1", None)
    assert a.snapshot("a1")["effect"] == "unchecked"


def test_probe_exception_is_indeterminate_not_verified():
    class Broken:
        def check(self, goal): raise RuntimeError("probe down")
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Done")
    assert a.check_effect("a1", Broken()) == Effect.PROBE_INDETERMINATE
    assert a.snapshot("a1")["effect"] == "probe_indeterminate"


def test_preexisting_state_true_does_not_prove_causation():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Done")
    a.check_effect("a1", MockTrustedWorld(True))
    assert a.snapshot("a1")["action_caused_change_proven"] is False


def test_fresh_false_probe_replaces_previous_true_at_probe():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Done")
    assert a.check_effect("a1", MockTrustedWorld(True)) == Effect.TRUE_AT_PROBE
    first = a.snapshot("a1")["effect_probe_sequence"]
    assert a.check_effect("a1", MockTrustedWorld(False)) == Effect.FALSE_AT_PROBE
    assert a.snapshot("a1")["effect_probe_sequence"] > first


def test_host_exit_receipt_not_forged_from_text():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "exit status: 0")
    assert a.snapshot("a1")["host_exit_status"] is None
    with pytest.raises(Error): a.host_exit("a1", "0")
    with pytest.raises(Error): a.host_exit("a1", True)


def test_audited_event_order_and_once_only_dispatch():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Done")
    ev = a.events()
    assert [e.sequence for e in ev] == [1,2,3]
    assert all(e.action_id == "a1" for e in ev)
    with pytest.raises(Error): a.submit("a1")
    with pytest.raises(Error): a.observe("a1", "Another output")
    with pytest.raises(Error): a.propose("a1", Goal("target-file-changed"))


def test_empty_goal_rejected():
    with pytest.raises(Error): Goal(" ")


def test_process_failure_not_inferred_from_output():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "fatal: failed")
    assert a.snapshot("a1")["process_exited_nonzero"] is False
    assert a.snapshot("a1")["host_exit_status"] is None


def test_non_boolean_probe_response_is_not_truth():
    class Misbehaving:
        def check(self, goal): return "yes"
    a = ledger()
    a.submit("a1")
    a.observe("a1", "Done")
    assert a.check_effect("a1", Misbehaving()) == Effect.PROBE_INDETERMINATE


def test_success_exit_does_not_imply_goal_true():
    a = ledger()
    a.submit("a1")
    a.observe("a1", "ok")
    a.host_exit("a1", 0)
    assert a.check_effect("a1", MockTrustedWorld(False)) == Effect.FALSE_AT_PROBE
    assert a.snapshot("a1")["host_exit_status"] == 0
