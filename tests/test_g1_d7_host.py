"""D7 host coordinator tests, all offline, no model or Harbor benchmark."""
import json
from pathlib import Path
import pytest

from harbor_terminus2.host_action_coordinator import (
    HostActionCoordinator, ExecutionReceipt
)
from harbor_terminus2.host_file_probe import FilePredicate, HostFileProbe
from hashlib import sha256
from contextlib import contextmanager

EXPECTED=b"TCSE_D7_EXPECTED\n"

class FakeHost:
    def __init__(self, callback):
        self.callback=callback
        self.calls=[]
        self.pauses=0
    def execute(self, command):
        self.calls.append(command)
        return self.callback(command)
    @contextmanager
    def quiesced(self):
        self.pauses+=1
        yield

def encode(command, completed=False, **extra):
    return json.dumps({"analysis":"claimed success","plan":"check the result",
        "commands":[{"keystrokes":command,"duration":1}],
        "task_complete":completed,**extra})

def setup(tmp_path, callback):
    p=FilePredicate("predeclared","output.bin",sha256(EXPECTED).hexdigest())
    probe=HostFileProbe(tmp_path,(p,))
    host=FakeHost(callback)
    service=HostActionCoordinator(run_id="host-run",goal=p,probe=probe,executor=host)
    return service,probe,host

def test_forged_success_in_terminal_cannot_certify(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"SUCCESS VERIFIED",0))
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("echo success\n",True),source_run_id="host-run")
    assert got.after_effect=="false_at_probe" and not got.task_solved_proven
    assert got.host_exit_code==0 and ex.pauses==1

def test_actual_file_success_is_time_local_only(tmp_path):
    def act(c):
        (tmp_path/"output.bin").write_bytes(EXPECTED)
        return ExecutionReceipt(True,"ERROR",1)
    a,p,ex=setup(tmp_path,act)
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("write\n"),source_run_id="host-run")
    assert got.after_effect=="true_at_probe" and got.before_effect=="false_at_probe"
    assert got.observed_false_to_true and got.host_exit_code==1
    assert not got.action_caused_change_proven and not got.task_solved_proven

def test_reject_missing_enter_without_exec(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"SUCCESS",0))
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("git status"),source_run_id="host-run")
    assert got.dispatch=="rejected" and ex.calls==[]
    assert a.ledger.snapshot("1")["effect"]=="unchecked"

def test_model_cannot_change_predicate_with_extra_field(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"PASS",0))
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("echo test\n",goal_id="evil"),source_run_id="host-run")
    assert got.after_effect=="false_at_probe" and a.ledger.snapshot("1")["goal"]=="predeclared"

def test_cross_run_refused_before_dispatch(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"",0))
    with p,pytest.raises(ValueError):
        a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="evil-run")
    assert not ex.calls and not a.ledger.events()

def test_bad_json_no_dispatch(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"",0))
    with p:
        out=a.adjudicate(action_id="a",agent_response="non-json",source_run_id="host-run")
    assert out.dispatch=="rejected" and ex.calls==[]

def test_empty_and_multiple_commands_rejected(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"",0))
    empty=json.dumps({"analysis":"","plan":"","commands":[]})
    many=json.dumps({"analysis":"","plan":"","commands":[
        {"keystrokes":"pwd\n"},{"keystrokes":"ls\n"}]})
    with p:
        assert a.adjudicate(action_id="a",agent_response=empty,source_run_id="host-run").dispatch=="rejected"
        assert a.adjudicate(action_id="b",agent_response=many,source_run_id="host-run").dispatch=="rejected"
    assert not ex.calls

def test_executor_refuses_dispatch_no_effect(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(False,"",None))
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert got.disposition=="rejected_without_dispatch" and got.after_effect is None
    assert ex.pauses==0

def test_pause_failure_cannot_certify(tmp_path):
    class FailingHost(FakeHost):
        @contextmanager
        def quiesced(self):
            raise RuntimeError("cannot pause")
            yield
    p=FilePredicate("predeclared","output.bin",sha256(EXPECTED).hexdigest())
    probe=HostFileProbe(tmp_path,(p,))
    a=HostActionCoordinator(run_id="host-run",goal=p,probe=probe,
        executor=FailingHost(lambda c:ExecutionReceipt(True,"claimed success",0)))
    with probe:
        got=a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert got.disposition=="observation_unavailable" and got.after_effect=="indeterminate"
    assert a.ledger.snapshot("1")["effect"]=="unchecked"

def test_preexisting_success_not_new_effect(tmp_path):
    (tmp_path/"output.bin").write_bytes(EXPECTED)
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"Done",0))
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert got.before_effect=="true_at_probe" and got.after_effect=="true_at_probe"
    assert got.observed_false_to_true is False

def test_unique_action_id_enforced(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"",0))
    with p:
        a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")
        with pytest.raises(ValueError):
            a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")

def test_recorded_host_exit_unknown_is_not_zero(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"pending",None))
    with p:
        result=a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert result.host_exit_code is None
    assert a.ledger.snapshot("1")["process_exited_nonzero"] is None

def test_no_probe_when_missing_dispatch_receipt(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(False,"",None))
    with p:
        got=a.adjudicate(action_id="1",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert not got.observed_false_to_true and ex.pauses==0


def test_transport_exception_abstains_not_rejected(tmp_path):
    def failure(c):
        raise RuntimeError("post-send connection closed")
    a,p,ex=setup(tmp_path,failure)
    with p:
        got=a.adjudicate(action_id="x",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert got.disposition=="dispatch_unknown"
    assert a.ledger.snapshot("x")["dispatch"]=="dispatch_unknown"
    assert not got.task_solved_proven and got.after_effect is None


def test_explicit_unknown_dispatch_not_a_failure(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(None,"Docker transport ambiguous",None))
    with p:
        got=a.adjudicate(action_id="x",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert got.dispatch=="unknown" and a.ledger.snapshot("x")["effect"]=="unchecked"
    assert ex.pauses==0


def test_exactly_two_probes_per_successful_window(tmp_path):
    a,p,ex=setup(tmp_path,lambda c:ExecutionReceipt(True,"DONE",0))
    original=p.sample
    sampled=[]
    def spy(goal):
        sampled.append(goal)
        return original(goal)
    p.sample=spy
    with p:
        got=a.adjudicate(action_id="x",agent_response=encode("pwd\n"),source_run_id="host-run")
    assert got.disposition=="postcondition_observed"
    assert len(sampled)==2  # pre and one host-owned post, not a conflicting third probe


def test_reserved_cli_status_must_not_be_treated_as_command_receipt(monkeypatch):
    from harbor_terminus2.host_action_coordinator import DockerExecutor
    from types import SimpleNamespace
    def fake(*args,**kwargs):
        return SimpleNamespace(returncode=125,stdout="",stderr="Error response from daemon")
    monkeypatch.setattr("harbor_terminus2.host_action_coordinator.subprocess.run",fake)
    report=DockerExecutor("host-chosen").execute("pwd\n")
    assert report.sent is None and report.host_exit_code is None
