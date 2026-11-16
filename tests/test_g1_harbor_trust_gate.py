"""D7 verifier-mode gate tests, all using native/sanitized Harbor results."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from zipfile import ZipFile
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("d7_gate",ROOT/"scripts/tcse_harbor_trust_gate.py")
M=importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name]=M
SPEC.loader.exec_module(M)
DATA=json.loads((ROOT/"tests/fixtures/tcse_d7_historical_mode_receipts.json").read_text())
HIST=DATA["trials"]


def test_actual_historical_evidence_blocks_d7():
    result=M.decide(HIST)
    assert result["decision"]==M.BLOCK
    assert not result["production_D7_accepted"] and not result["D8_authorized"]
    assert all(p["verifier_mode"]=="shared" for xs in result["tasks"].values() for p in xs)


def test_separate_verifier_still_does_not_certify_action_boundary():
    rows=[{**r,"verifier_mode":"separate"} for r in HIST]
    assert M.decide(rows)["decision"]==M.UNPROVEN


def test_missing_trial_fails_closed():
    assert M.decide([r for r in HIST if r["task"]=="terminal-bench/fix-git"])["decision"]==M.INVALID


def test_changed_task_ref_fails_closed():
    rows=[{**r,"task_ref":"sha256:other"} for r in HIST]
    assert M.decide(rows)["decision"]==M.INVALID


def test_unknown_verifier_mode_fails_closed():
    rows=[{**r,"verifier_mode":None} for r in HIST]
    assert M.decide(rows)["decision"]==M.INVALID


def test_malformed_result_fails_closed():
    rows=HIST+[{"record_id":"bad","invalid_json":True}]
    assert M.decide(rows)["decision"]==M.INVALID


def test_task_reward_validity_not_equivalent_to_trust():
    rows=[{**r,"reward_valid":True,"has_verifier_result":True} for r in HIST]
    assert M.decide(rows)["decision"]==M.BLOCK


def test_read_native_harbor_zip_without_extraction(tmp_path):
    file=tmp_path/"archive.zip"
    with ZipFile(file,"w") as z:
        for index, row in enumerate(HIST):
            info={"task_name":row["task"],"task_checksum":row["task_checksum"],
                  "config":{"task":{"ref":row["task_ref"]}},
                  "verifier_environment_mode":row["verifier_mode"],
                  "reward_valid":True,"verifier_result":{"rewards":{"reward":0}}}
            z.writestr(f"runs/coding/jobs/smoke/task{index}/result.json",json.dumps(info))
        z.writestr("runs/coding/jobs/smoke/result.json",json.dumps({"n_total_trials":4}))
    assert M.decide(M.inspect_zip(file))["decision"]==M.BLOCK


def test_malicious_archive_path_is_never_extracted(tmp_path):
    f=tmp_path/"archive.zip"
    with ZipFile(f,"w") as z:z.writestr("../outside/result.json",json.dumps({"junk":True}))
    M.inspect_zip(f)
    assert not (tmp_path.parent/"outside").exists()


def test_same_trial_digest_reproducible():
    sample=json.dumps({"task_name":"terminal-bench/extract-elf","config":{}},
                      sort_keys=True).encode()
    x=M.extract_one(sample,record_id="x")
    assert x["raw_result_sha256"]==hashlib.sha256(sample).hexdigest()


def test_unexpected_task_is_insufficient():
    assert M.decide(HIST+[dict(HIST[0],task="terminal-bench/unknown")])["decision"]==M.INVALID
