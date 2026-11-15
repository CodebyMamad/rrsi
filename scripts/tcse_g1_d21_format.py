"""Compare plain, JSON-object, and JSON-schema Ollama outputs on frozen D2 prompts.

Twelve synthetic requests only. Never execute any generated shell command.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
PARSER=ROOT/"third_party/harbor_terminus2/terminus_json_plain_parser.py"
FROZEN=ROOT/"scripts/tcse_g1_d2_probe.py"
MODES=("plain","json_object","json_schema")
SCHEDULE=[
    ("short","plain"),("error","json_schema"),("long","json_object"),
    ("repeat","plain"),("short","json_object"),("error","plain"),
    ("long","json_schema"),("repeat","json_object"),("short","json_schema"),
    ("error","json_object"),("long","plain"),("repeat","json_schema"),
]
SCHEMA={
 "type":"object",
 "properties":{
   "analysis":{"type":"string"},
   "plan":{"type":"string"},
   "commands":{"type":"array","maxItems":4,"items":{"type":"object",
      "properties":{"keystrokes":{"type":"string","minLength":1},
                    "duration":{"type":"number","minimum":0,"maximum":60}},
      "required":["keystrokes","duration"],"additionalProperties":False}},
   "task_complete":{"type":"boolean"}
 },
 "required":["analysis","plan","commands","task_complete"],
 "additionalProperties":False
}

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec)
    sys.modules[name]=mod
    spec.loader.exec_module(mod)
    return mod

def source_prompts():
    cases=dict(load(FROZEN,"d21_frozen_source").samples())
    if tuple(cases)!=("short","error","long","repeat"):
        raise RuntimeError("Frozen D2 prompts changed")
    return cases

def valid_schema(obj):
    if not isinstance(obj,dict) or set(obj)!={"analysis","plan","commands","task_complete"}: return False
    if not isinstance(obj["analysis"],str) or not isinstance(obj["plan"],str): return False
    if type(obj["task_complete"]) is not bool: return False
    commands=obj["commands"]
    if not isinstance(commands,list) or len(commands)>4: return False
    for c in commands:
        if not isinstance(c,dict) or set(c)!={"keystrokes","duration"}: return False
        if not isinstance(c["keystrokes"],str) or not c["keystrokes"]: return False
        d=c["duration"]
        if isinstance(d,bool) or not isinstance(d,(int,float)) or not 0<=d<=60: return False
    return True

def kwargs(model,url,prompt,mode):
    x={"model":model,"api_base":url,"messages":[{"role":"user","content":prompt}],
       "max_tokens":350,"temperature":0,"timeout":150,"num_retries":0}
    if mode=="json_object": x["response_format"]={"type":"json_object"}
    elif mode=="json_schema":
        x["response_format"]={"type":"json_schema","json_schema":{
            "name":"terminus2_command","strict":True,"schema":SCHEMA}}
    elif mode!="plain": raise ValueError(mode)
    return x

def analyse(content,parser):
    try:
        obj=json.loads(content)
        strict=isinstance(obj,dict)
    except (ValueError,TypeError):
        obj=None
        strict=False
    p=parser.parse_response(content)
    commands=[c.keystrokes for c in p.commands]
    return {"strict_json":strict,"schema_valid":valid_schema(obj),
      "parser_valid":not bool(p.error),"parser_error":p.error[:160] if p.error else None,
      "parser_warning":p.warning[:160] if p.warning else None,
      "command_count":len(commands),"single_command":len(commands)==1,
      "repeats_failed_command":any("cd /impossible/synthetic/path" in c for c in commands),
      "command_hashes":[hashlib.sha256(c.encode()).hexdigest() for c in commands]}

def run(model,url,dest):
    from litellm import completion
    parser=load(PARSER,"d21_original_parser").TerminusJSONPlainParser()
    prompts=source_prompts()
    rows=[]
    rec={"phase":"D2.1_SYNTHEIC_ONLY","model":model,"provider":"litellm/ollama_chat",
      "modes":list(MODES),"schedule":SCHEDULE,"temperature":0,"max_tokens":350,
      "request_timeout_s":150,
      "original_D2_prompt_sha256":{k:hashlib.sha256(v.encode()).hexdigest() for k,v in prompts.items()},
      "schema_sha256":hashlib.sha256(json.dumps(SCHEMA,sort_keys=True).encode()).hexdigest(),
      "no_benchmark_tasks":True,"no_commands_executed":True,
      "rows":rows,"status":"running"}
    dest.parent.mkdir(parents=True,exist_ok=True)
    try:
        for case,mode in SCHEDULE:
            t=time.monotonic()
            row={"case":case,"mode":mode}
            try:
                resp=completion(**kwargs(model,url,prompts[case],mode))
                content=resp.choices[0].message.content or ""
                row.update(analyse(content,parser))
                row["response_sha256"]=hashlib.sha256(content.encode()).hexdigest()
                row["synthetic_response"]=content
                row["prompt_tokens"]=getattr(resp.usage,"prompt_tokens",None)
                row["completion_tokens"]=getattr(resp.usage,"completion_tokens",None)
                row["finish_reason"]=resp.choices[0].finish_reason
                row["exception_type"]=None
            except Exception as e:
                row.update(strict_json=False,schema_valid=False,parser_valid=False,
                           command_count=0,single_command=False,
                           exception_type=type(e).__name__,exception_summary=str(e)[:250])
            row["seconds"]=round(time.monotonic()-t,3)
            rows.append(row)
            dest.write_text(json.dumps(rec,indent=2,ensure_ascii=False)+"\n")
            print(f"D21 case={case} mode={mode} sec={row['seconds']:.2f} strict={row['strict_json']} schema={row['schema_valid']} parse={row['parser_valid']}",flush=True)
        summary={}
        for mode in MODES:
            group=[r for r in rows if r["mode"]==mode]
            summary[mode]={"n":len(group),
                "strict_json":sum(bool(r["strict_json"]) for r in group),
                "schema_valid":sum(bool(r["schema_valid"]) for r in group),
                "parser_valid":sum(bool(r["parser_valid"]) for r in group),
                "single_command":sum(bool(r["single_command"]) for r in group),
                "exceptions":sum(bool(r.get("exception_type")) for r in group),
                "median_seconds":round(statistics.median(r["seconds"] for r in group),3),
                "repeats_failed_command":sum(bool(r.get("repeats_failed_command")) for r in group)}
        rec["summary"]=summary
        q=summary["json_schema"]
        rec["decision"]=("D21_PRECHECK_PASS" if q["schema_valid"]==4 and
            q["parser_valid"]==4 and q["single_command"]==4 and
            q["exceptions"]==0 and q["median_seconds"]<=35 and
            q["repeats_failed_command"]==0 else "D21_REQUIRES_DIAGNOSIS")
        rec["status"]="complete"
        print(json.dumps({"decision":rec["decision"],"summary":summary},indent=2),flush=True)
        return 0
    finally:
        dest.write_text(json.dumps(rec,indent=2,ensure_ascii=False)+"\n")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--model",default="ollama_chat/qwen2.5-coder:1.5b")
    ap.add_argument("--url",default="http://127.0.0.1:11434")
    ap.add_argument("--output",type=Path,default=Path("tcse_d21_receipts/results.json"))
    args=ap.parse_args()
    raise SystemExit(run(args.model,args.url,args.output))
