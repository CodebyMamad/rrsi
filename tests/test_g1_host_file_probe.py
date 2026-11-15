"""D6 isolated filesystem probe tests; no model or Terminal-Bench tasks."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/"third_party/harbor_terminus2/host_file_probe.py"
S=importlib.util.spec_from_file_location("tcse_d6_host_probe",P)
M=importlib.util.module_from_spec(S)
sys.modules[S.name]=M
S.loader.exec_module(M)
FilePredicate, Probe, Session, Status = M.FilePredicate, M.HostFileProbe, M.HostVerificationSession, M.Status

def digest(b):
    return hashlib.sha256(b).hexdigest()

def fresh(root, expected=b"GOOD"):
    return Probe(root,(FilePredicate("verified-output","result.bin",digest(expected)),))

def test_missing_file_is_false(tmp_path):
    with fresh(tmp_path) as p:
        assert p.sample("verified-output").status is Status.FALSE

def test_exact_file_is_true(tmp_path):
    (tmp_path/"result.bin").write_bytes(b"GOOD")
    with fresh(tmp_path) as p:
        x=p.sample("verified-output")
        assert x.status is Status.TRUE and x.actual_sha256==digest(b"GOOD")

def test_wrong_file_content_false(tmp_path):
    (tmp_path/"result.bin").write_bytes(b"WRONG")
    with fresh(tmp_path) as p:
        assert p.sample("verified-output").status is Status.FALSE

def test_symlink_escape_is_indeterminate(tmp_path):
    outside=tmp_path.parent/"outside-probe-file"
    outside.write_bytes(b"GOOD")
    (tmp_path/"result.bin").symlink_to(outside)
    try:
        with fresh(tmp_path) as p:
            assert p.sample("verified-output").status is Status.UNKNOWN
    finally:
        outside.unlink()

def test_reject_escape_and_subdirectories(tmp_path):
    for filename in ("../outside", "folder/result.bin", "/tmp/out", "..", r"..\\secret", ""):
        with pytest.raises(M.ProbeError):
            FilePredicate("id",filename,digest(b"GOOD"))

def test_reject_arbitrary_goal(tmp_path):
    with fresh(tmp_path) as p:
        with pytest.raises(M.ProbeError): p.sample("model-invented-goal")

def test_reject_duplicate_predicate(tmp_path):
    spec=FilePredicate("id","a",digest(b"a"))
    with pytest.raises(M.ProbeError):
        Probe(tmp_path,(spec,spec))

def test_preexisting_true_does_not_imply_action_effect(tmp_path):
    (tmp_path/"result.bin").write_bytes(b"GOOD")
    with fresh(tmp_path) as p:
        w=Session("run-1",p)
        assert w.begin("a","verified-output",source_run_id="run-1").status is Status.TRUE
        receipt=w.finish("a",source_run_id="run-1")
        assert receipt.after is Status.TRUE and not receipt.observed_false_to_true
        assert receipt.action_causation_proven is False

def test_false_to_true_is_observed_not_causal_proof(tmp_path):
    with fresh(tmp_path) as p:
        w=Session("run-1",p)
        assert w.begin("a","verified-output",source_run_id="run-1").status is Status.FALSE
        (tmp_path/"result.bin").write_bytes(b"GOOD")
        receipt=w.finish("a",source_run_id="run-1")
        assert receipt.observed_false_to_true
        assert not receipt.action_causation_proven and not receipt.task_solved_proven

def test_partial_write_after_nonzero_exit_can_satisfy_predicate(tmp_path):
    with fresh(tmp_path) as p:
        w=Session("run-1",p)
        w.begin("a","verified-output",source_run_id="run-1")
        # Host records command as failed; observed side effect can still be true.
        process_return_code=1
        (tmp_path/"result.bin").write_bytes(b"GOOD")
        receipt=w.finish("a",source_run_id="run-1")
        assert process_return_code==1 and receipt.after is Status.TRUE

def test_repeat_check_detects_new_contents_not_cached(tmp_path):
    f=tmp_path/"result.bin"
    f.write_bytes(b"GOOD")
    with fresh(tmp_path) as p:
        assert p.sample("verified-output").status is Status.TRUE
        f.write_bytes(b"BAD")
        assert p.sample("verified-output").status is Status.FALSE
        f.write_bytes(b"GOOD")
        assert p.sample("verified-output").status is Status.TRUE

def test_same_transcript_opposite_worlds(tmp_path):
    a=tmp_path/"world_a"; b=tmp_path/"world_b"
    a.mkdir(); b.mkdir()
    (a/"result.bin").write_bytes(b"GOOD")
    (b/"result.bin").write_bytes(b"WRONG")
    output="Done\\n"; exit_code=0
    with fresh(a) as p_a, fresh(b) as p_b:
        assert output=="Done\\n" and exit_code==0
        assert p_a.sample("verified-output").status is Status.TRUE
        assert p_b.sample("verified-output").status is Status.FALSE

def test_intervening_host_action_is_recorded(tmp_path):
    with fresh(tmp_path) as p:
        w=Session("run-A",p)
        w.begin("a","verified-output",source_run_id="run-A")
        w.note_intervention("a",source_run_id="run-A")
        (tmp_path/"result.bin").write_bytes(b"GOOD")
        r=w.finish("a",source_run_id="run-A")
        assert r.external_intervention_recorded and not r.action_causation_proven

def test_foreign_run_rejected(tmp_path):
    with fresh(tmp_path) as p:
        w=Session("run-A",p)
        with pytest.raises(M.ProbeError):
            w.begin("a","verified-output",source_run_id="run-B")
        w.begin("a","verified-output",source_run_id="run-A")
        with pytest.raises(M.ProbeError):
            w.finish("a",source_run_id="run-B")

def test_stale_repeat_receipt_rejected(tmp_path):
    with fresh(tmp_path) as p:
        w=Session("run-A",p)
        w.begin("a","verified-output",source_run_id="run-A")
        w.finish("a",source_run_id="run-A")
        with pytest.raises(M.ProbeError):
            w.finish("a",source_run_id="run-A")
        with pytest.raises(M.ProbeError):
            w.begin("a","verified-output",source_run_id="run-A")

def test_oversized_file_indeterminate(tmp_path):
    (tmp_path/"result.bin").write_bytes(b"G"*100)
    p=Probe(tmp_path,(FilePredicate("verified-output","result.bin",digest(b"GOOD"),max_bytes=5),))
    try:
        assert p.sample("verified-output").status is Status.UNKNOWN
    finally:
        p.close()

def test_directory_instead_of_file_indeterminate(tmp_path):
    (tmp_path/"result.bin").mkdir()
    with fresh(tmp_path) as p:
        assert p.sample("verified-output").status is Status.UNKNOWN

def test_read_error_indeterminate(tmp_path,monkeypatch):
    (tmp_path/"result.bin").write_bytes(b"GOOD")
    with fresh(tmp_path) as p:
        original=M.os.read
        def fail(fd,length):
            raise OSError("synthetic failing read")
        with monkeypatch.context() as patch:
            patch.setattr(M.os,"read",fail)
            assert p.sample("verified-output").status is Status.UNKNOWN
        assert M.os.read is original

def test_in_place_mutation_during_read_indeterminate(tmp_path,monkeypatch):
    file=tmp_path/"result.bin"
    file.write_bytes(b"GOOD")
    with fresh(tmp_path) as p:
        original=M.os.read
        used=[False]
        def mutate(fd,length):
            blob=original(fd,length)
            if blob and not used[0]:
                used[0]=True
                file.write_bytes(b"BADDIFFERENT")
            return blob
        with monkeypatch.context() as patch:
            patch.setattr(M.os,"read",mutate)
            assert p.sample("verified-output").status is Status.UNKNOWN

def test_rename_replacement_during_read_indeterminate(tmp_path,monkeypatch):
    file=tmp_path/"result.bin"
    file.write_bytes(b"GOOD")
    temp=tmp_path/"fresh.bin"
    temp.write_bytes(b"WRONG")
    with fresh(tmp_path) as p:
        original=M.os.read
        used=[False]
        def swap(fd,length):
            blob=original(fd,length)
            if blob and not used[0]:
                used[0]=True
                os.replace(temp,file)
            return blob
        with monkeypatch.context() as patch:
            patch.setattr(M.os,"read",swap)
            assert p.sample("verified-output").status is Status.UNKNOWN

def test_closed_probe_rejected(tmp_path):
    p=fresh(tmp_path)
    p.close()
    with pytest.raises(M.ProbeError): p.sample("verified-output")

def test_probe_adapter_bridge_to_d5(tmp_path):
    sys.path.insert(0,str(ROOT/"third_party"))
    from harbor_terminus2.evidence_ledger import EvidenceLedger,Goal,Effect
    f=tmp_path/"result.bin"
    with fresh(tmp_path) as p:
        l=EvidenceLedger("run-1")
        l.propose("a",Goal("verified-output"))
        l.submit("a",source_run_id="run-1")
        l.observe("a","Done (untrusted)",source_run_id="run-1")
        assert l.check_effect("a",p,source_run_id="run-1") is Effect.FALSE_AT_PROBE
        f.write_bytes(b"GOOD")
        assert l.check_effect("a",p,source_run_id="run-1") is Effect.TRUE_AT_PROBE
        assert l.snapshot("a")["task_solved_proven"] is False
