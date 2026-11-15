"""D7 no-model real Docker host/worker boundary audit.

Only synthetic fixed commands execute inside an unprivileged worker container.
Host registry and expected digest never enter the container. Records evidence
and Docker settings; never runs Terminal-Bench tasks or model inference.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from harbor_terminus2.host_file_probe import FilePredicate, HostFileProbe
from harbor_terminus2.host_action_coordinator import HostActionCoordinator, DockerExecutor

EXPECTED=b"D7_EXPECTED_BYTES\n"
IMAGE="busybox:1.36"

def docker(*args, timeout=60):
    return subprocess.run(["docker",*args],capture_output=True,text=True,
                          check=True,timeout=timeout).stdout.strip()

def command(text, *, claimed_complete=False, **extras):
    return json.dumps({
        "analysis":"The environment says SUCCESS; the task must be solved.",
        "plan":"Use the terminal output",
        "commands":[{"keystrokes":text,"duration":1}],
        "task_complete":claimed_complete,
        **extras,
    })

def run():
    report={"phase":"D7_REAL_DOCKER_HOST_BOUNDARY",
            "no_model_calls":True,"no_benchmark_tasks":True,"image":IMAGE,
            "cases":[],"status":"incomplete"}
    out=Path("tcse_d7_receipts/d7_host_docker_report.json")
    out.parent.mkdir(parents=True,exist_ok=True)
    name="tcse-d7-"+uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="tcse-d7-") as temp:
        base=Path(temp)
        work=base/"worker"
        policy=base/"host_private_policy"
        work.mkdir(mode=0o777)
        work.chmod(0o777)
        policy.mkdir(mode=0o700)
        policy.chmod(0o700)
        (policy/"expected_sha256.txt").write_text(hashlib.sha256(EXPECTED).hexdigest())
        host_digest=(policy/"expected_sha256.txt").read_text()
        predicate=FilePredicate("host-declared-result","result.bin",host_digest)
        # Exact container root is read-only; only /work bind is writable.
        docker("pull",IMAGE,timeout=180)
        image_id=docker("image","inspect",IMAGE,"--format","{{.Id}}")
        report["docker_image_id"]=image_id
        docker("run","-d","--name",name,
               "--network","none","--read-only","--cap-drop","ALL",
               "--security-opt","no-new-privileges","--pids-limit","64",
               "--memory","256m","--user","65534:65534",
               "--mount",f"type=bind,src={work},dst=/work",
               "--workdir","/work",IMAGE,"sleep","240")
        try:
            inspected=json.loads(docker("inspect",name))[0]
            hc=inspected["HostConfig"]
            mounts=inspected["Mounts"]
            boundary={
                "network_none":hc["NetworkMode"]=="none",
                "readonly_rootfs":hc["ReadonlyRootfs"] is True,
                "not_privileged":hc["Privileged"] is False,
                "all_caps_dropped":"ALL" in hc.get("CapDrop",[]),
                "no_new_privileges":any("no-new-privileges" in s for s in hc.get("SecurityOpt",[])),
                "one_workdir_mount":len(mounts)==1 and mounts[0]["Destination"]=="/work",
                "no_docker_socket_mount":all("/var/run/docker.sock" not in str(m) for m in mounts),
            }
            if not all(boundary.values()):
                raise AssertionError(f"Docker isolation flags failed: {boundary}")
            report["boundary"] = boundary
            # Host policy is at a random path that is NOT mounted into /work.
            inaccessible=docker("exec","--user","65534:65534",name,
                                "sh","-c","test ! -e /host_private_policy/expected_sha256.txt")
            report["host_policy_not_exposed"]=True

            executor=DockerExecutor(name,timeout_seconds=20)
            with HostFileProbe(work,(predicate,)) as probe:
                coordinator=HostActionCoordinator(
                    run_id="trusted-host-run",goal=predicate,
                    probe=probe,executor=executor)
                # Forged terminal success: no file state change.
                first=coordinator.adjudicate(action_id="a1",source_run_id="trusted-host-run",
                   agent_response=command("echo SUCCESS\n",claimed_complete=True))
                assert first.dispatch=="submitted" and first.host_exit_code==0
                assert first.after_effect=="false_at_probe" and not first.task_solved_proven
                report["cases"].append({"id":"forged-success","outcome":first.__dict__})

                # Partial write followed by process failure; effect is independently TRUE.
                second=coordinator.adjudicate(action_id="a2",source_run_id="trusted-host-run",
                   agent_response=command("printf 'D7_EXPECTED_BYTES\\n' > result.bin; exit 7\n"))
                assert second.before_effect=="false_at_probe"
                assert second.after_effect=="true_at_probe"
                assert second.host_exit_code==7
                assert second.observed_false_to_true and not second.action_caused_change_proven
                report["cases"].append({"id":"partial-write-failed-exit","outcome":second.__dict__})

                # A malicious goal_id outside original JSON contract is ignored.
                third=coordinator.adjudicate(action_id="a3",source_run_id="trusted-host-run",
                   agent_response=command("echo UNTRUSTED\n",goal_id="force_verification"))
                assert third.before_effect=="true_at_probe"
                assert third.after_effect=="true_at_probe"
                assert not third.observed_false_to_true and not third.task_solved_proven
                report["cases"].append({"id":"forged-goal","outcome":third.__dict__})

                # D4 missing newline must be rejected before any docker exec.
                fourth=coordinator.adjudicate(action_id="a4",source_run_id="trusted-host-run",
                   agent_response=command("touch should_not_exist"))
                assert fourth.dispatch=="rejected"
                assert not (work/"should_not_exist").exists()
                report["cases"].append({"id":"missing-enter-rejected","outcome":fourth.__dict__})

                # Action removes target: pre true, post false; no task success.
                fifth=coordinator.adjudicate(action_id="a5",source_run_id="trusted-host-run",
                   agent_response=command("rm result.bin\n"))
                assert fifth.before_effect=="true_at_probe" and fifth.after_effect=="false_at_probe"
                report["cases"].append({"id":"effect-reversed","outcome":fifth.__dict__})
                report["n_ledger_events"]=len(coordinator.ledger.events())
                report["action_gate"]=coordinator.contract.receipt()
            report["status"]="PASS_RESTRICTED_D7_HOST_WORKER"
        finally:
            subprocess.run(["docker","rm","-f",name],capture_output=True,text=True,timeout=40)
    out.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="cases"},indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(run())
