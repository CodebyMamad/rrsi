"""Pure and Harbor-backed action-contract tests. No model or benchmark calls."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/"third_party"/"harbor_terminus2"/"action_contract.py"
spec=importlib.util.spec_from_file_location("tcse_action_contract_under_test", MOD)
mod=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=mod
spec.loader.exec_module(mod)
Contract=mod.ActionContract

def test_off_is_inert():
    c=Contract()
    assert c.validate(["git checkout <commit-hash>"]) is None
    assert c.receipt()["checked_batches"]==0

def test_missing_enter_detected():
    c=Contract("strict")
    message=c.validate(["git checkout <commit-hash>"])
    assert "NOT sent" in message and "position(s) 1" in message
    assert c.receipt()["rejected_commands"]==1

def test_batch_atomic_rejection():
    c=Contract("strict")
    assert "position(s) 2" in c.validate(["pwd\n","git merge <commit-hash>"])
    assert c.receipt()["checked_commands"]==2 and c.receipt()["rejected_batches"]==1

def test_valid_commands_allowed():
    c=Contract("strict")
    assert c.validate(["pwd\n","ls -la\n","echo x\r"]) is None

def test_empty_wait_is_allowed():
    assert Contract("strict").validate([""]) is None

def test_known_terminal_control_keys_allowed():
    c=Contract("strict")
    for text in ("C-c","C-d","C-m","Enter","KPEnter"):
        assert c.validate([text]) is None

def test_partial_interactive_typing_not_in_admitted_class():
    assert Contract("strict").validate(["echo unfinished"]) is not None

def test_private_command_not_logged_in_error_or_metrics():
    secret="USER_PRIVATE_SAMPLE_TOKEN"
    c=Contract("strict")
    e=c.validate([secret])
    assert secret not in e and secret not in json.dumps(c.receipt())

def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        Contract("blindly_rewrite_commands")

def test_orig_parser_final_command_without_enter_not_previously_rejected():
    parser_file=ROOT/"third_party"/"harbor_terminus2"/"terminus_json_plain_parser.py"
    s=importlib.util.spec_from_file_location("tcse_parser_for_contract_test",parser_file)
    p=importlib.util.module_from_spec(s)
    sys.modules[s.name]=p
    s.loader.exec_module(p)
    raw=json.dumps({"analysis":"test","plan":"test","commands":[{"keystrokes":"git status","duration":1}]})
    got=p.TerminusJSONPlainParser().parse_response(raw)
    assert not got.error and len(got.commands)==1
    assert Contract("strict").validate([c.keystrokes for c in got.commands]) is not None

def test_real_harbor_handler_blocks_pre_dispatch():
    pytest.importorskip("harbor")
    sys.path.insert(0,str(ROOT/"third_party"))
    from harbor_terminus2.terminus_2 import Terminus2
    from harbor_terminus2.terminus_json_plain_parser import TerminusJSONPlainParser
    from harbor.llms.base import LLMResponse
    fake=object.__new__(Terminus2)
    fake._parser=TerminusJSONPlainParser()
    fake._tcse_action_contract=Contract("strict")
    async def fake_query(*args,**kwargs):
        return LLMResponse(content=json.dumps({
            "analysis":"try", "plan":"try",
            "commands":[{"keystrokes":"git status","duration":1}], "task_complete":True}))
    fake._query_llm=fake_query
    result=asyncio.run(Terminus2._handle_llm_interaction(fake,None,"unused"))
    commands,completed,feedback=result[0],result[1],result[2]
    assert commands == [] and not completed
    assert "NOT sent" in feedback
    assert fake._tcse_action_contract.rejected_batches==1

def test_real_harbor_handler_accepts_valid_submission():
    pytest.importorskip("harbor")
    sys.path.insert(0,str(ROOT/"third_party"))
    from harbor_terminus2.terminus_2 import Terminus2
    from harbor_terminus2.terminus_json_plain_parser import TerminusJSONPlainParser
    from harbor.llms.base import LLMResponse
    fake=object.__new__(Terminus2)
    fake._parser=TerminusJSONPlainParser()
    fake._tcse_action_contract=Contract("strict")
    async def fake_query(*args,**kwargs):
        return LLMResponse(content=json.dumps({
            "analysis":"try", "plan":"try",
            "commands":[{"keystrokes":"git status\n","duration":1}]}))
    fake._query_llm=fake_query
    result=asyncio.run(Terminus2._handle_llm_interaction(fake,None,"unused"))
    assert len(result[0])==1 and result[0][0].keystrokes=="git status\n"
    assert fake._tcse_action_contract.rejected_batches==0
