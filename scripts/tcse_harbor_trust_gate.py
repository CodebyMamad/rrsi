#!/usr/bin/env python3
"""D7 host-side Harbor trust gate; fail closed on evidence from shared verifiers.

This reads native Harbor result.json files or an existing receipt ZIP.
It DOES NOT execute benchmark tasks, touch hidden tests, or grant any trust
based on the value of the reward. A separate verifier alone never proves
the per-action host evidence boundary required by D7.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

EXPECTED = {
    "terminal-bench/extract-elf": "sha256:1ef31d566be4fe3459d5368621ae7ef7a31b23ef675737e473bbc43c8c7b3fce",
    "terminal-bench/fix-git": "sha256:16948b980df9d96de616a205f5acca1c5d395de83ff4f8ffabcafacb93226f2e",
}
BLOCK = "D7_BLOCKED_SHARED_VERIFIER"
UNPROVEN = "D7_HOST_ACTION_RECEIPTS_MISSING"
INVALID = "D7_INSUFFICIENT_OR_CHANGED_EVIDENCE"


def extract_one(data: bytes, *, record_id: str):
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return {"record_id":record_id,"invalid_json":True}
    if not isinstance(result, dict) or "task_name" not in result:
        return None  # ignore the parent Harbor job summary
    cfg = result.get("config") or {}
    task = cfg.get("task") or {}
    return {
        "record_id": record_id,
        "task": result.get("task_name"),
        "task_ref": task.get("ref"),
        "task_checksum": result.get("task_checksum"),
        "verifier_mode": result.get("verifier_environment_mode"),
        "reward_valid": result.get("reward_valid"),
        "has_verifier_result": result.get("verifier_result") is not None,
        "raw_result_sha256": hashlib.sha256(data).hexdigest(),
    }


def inspect_zip(archive: str | Path):
    out=[]
    with ZipFile(archive) as z:
        for name in z.namelist():
            if not name.endswith("/result.json"):
                continue
            # Do not unpack archive paths; only read member bytes.
            row=extract_one(z.read(name),record_id=name)
            if row is not None:
                out.append(row)
    return out


def inspect_results(root: str | Path):
    path=Path(root)
    return [
        row for x in sorted(path.rglob("result.json"))
        if (row:=extract_one(x.read_bytes(),record_id=str(x))) is not None
    ]


def decide(rows):
    by_task={task:[] for task in EXPECTED}
    problems=[]
    for r in rows:
        if r.get("invalid_json"):
            problems.append("malformed_json_result")
            continue
        task=r["task"]
        if task in by_task:
            by_task[task].append(r)
        else:
            problems.append("unexpected_task")
    for task, observations in by_task.items():
        if not observations:
            problems.append("missing_"+task)
        for r in observations:
            if r["task_ref"] != EXPECTED[task]:
                problems.append("task_ref_changed_"+task)
            if not isinstance(r["task_checksum"],str) or len(r["task_checksum"])!=64:
                problems.append("missing_task_checksum_"+task)
            if r["verifier_mode"] not in ("shared","separate"):
                problems.append("unknown_verifier_mode_"+task)
    if problems:
        verdict=INVALID
    elif any(r["verifier_mode"]=="shared" for entries in by_task.values() for r in entries):
        verdict=BLOCK
    else:
        # Even a verifier container independent of the worker does NOT give
        # trusted per-action stdout/exit/effect attribution.
        verdict=UNPROVEN
    return {
        "schema":"TCSE-D7-HARBOR-TRUST-GATE-v1",
        "decision":verdict,
        "production_D7_accepted":False,
        "D8_authorized":False,
        "note":"Official Harbor verifier reward is NOT a host-action receipt; shared mode violates the required verifier isolation.",
        "problems":sorted(set(problems)),
        "total_trials":len(rows),
        "tasks":{k:[{
            "record_id":v["record_id"],
            "task_ref":v["task_ref"],
            "task_checksum":v["task_checksum"],
            "verifier_mode":v["verifier_mode"],
            "reward_valid":v["reward_valid"],
            "has_verifier_result":v["has_verifier_result"],
            "raw_result_sha256":v["raw_result_sha256"],
        } for v in val] for k,val in by_task.items()},
    }


def main():
    p=argparse.ArgumentParser()
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive",type=Path)
    group.add_argument("--results-root",type=Path)
    group.add_argument("--fixture",type=Path,help="sanitized, host-generated summary fixture")
    p.add_argument("--output",type=Path)
    p.add_argument("--expect-blocked",action="store_true",
                   help="CI assertion only: succeed if historical evidence is BLOCKED")
    a=p.parse_args()
    if a.archive:
        trials=inspect_zip(a.archive)
    elif a.results_root:
        trials=inspect_results(a.results_root)
    else:
        data=json.loads(a.fixture.read_text())
        trials=data["trials"]
    report=decide(trials)
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"decision":report["decision"],
        "production_D7_accepted":False,
        "D8_authorized":False,
        "total_trials":report["total_trials"],
        "problems":report["problems"],
        "modes":{k:[x["verifier_mode"] for x in v] for k,v in report["tasks"].items()}},
        sort_keys=True))
    if a.expect_blocked:
        return 0 if report["decision"]==BLOCK else 3
    return 3  # NEVER label D7 production accepted on this restricted evidence.


if __name__=="__main__":
    raise SystemExit(main())
