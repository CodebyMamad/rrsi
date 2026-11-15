"""Offline D2.1 tests; no model or network calls."""
import importlib.util
import json
from pathlib import Path
R=Path(__file__).resolve().parents[1]
sp=importlib.util.spec_from_file_location("d21_under_test",R/"scripts/tcse_g1_d21_format.py")
m=importlib.util.module_from_spec(sp)
sp.loader.exec_module(m)
GOOD={"analysis":"inspect","plan":"read only","commands":[{"keystrokes":"pwd\n","duration":1}],"task_complete":False}

def test_reuse_frozen_prompts():
    assert tuple(m.source_prompts())==("short","error","long","repeat")

def test_valid_schema():
    assert m.valid_schema(GOOD)

def test_no_extra_fields():
    assert not m.valid_schema({**GOOD,"invented":1})

def test_invalid_command_rejected():
    assert not m.valid_schema({**GOOD,"commands":[{"keystrokes":42,"duration":1}]})

def test_missing_complete_rejected():
    d=GOOD.copy(); d.pop("task_complete")
    assert not m.valid_schema(d)

def test_options_match_except_format():
    a=m.kwargs("ollama_chat/test","http://localhost:11434","prompt","plain")
    b=m.kwargs("ollama_chat/test","http://localhost:11434","prompt","json_object")
    c=m.kwargs("ollama_chat/test","http://localhost:11434","prompt","json_schema")
    assert "response_format" not in a and b["response_format"]["type"]=="json_object"
    assert c["response_format"]["json_schema"]["schema"]==m.SCHEMA
    assert a["temperature"]==b["temperature"]==c["temperature"]==0

def test_original_parser_agrees_with_schema_valid():
    p=m.load(m.PARSER,"d21_test_parser").TerminusJSONPlainParser()
    obj=m.analyse(json.dumps(GOOD),p)
    assert obj["schema_valid"] and obj["parser_valid"] and obj["single_command"]

def test_strict_json_vs_parser_are_distinct():
    p=m.load(m.PARSER,"d21_test_parser2").TerminusJSONPlainParser()
    obj=m.analyse("Response:\n"+json.dumps(GOOD),p)
    assert not obj["strict_json"] and obj["parser_valid"]
