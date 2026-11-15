"""Offline tests of the original parser and the optional progress guard."""
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULES = ROOT / "third_party" / "harbor_terminus2"


def load_module(stem):
    spec = importlib.util.spec_from_file_location("tcse_test_" + stem, MODULES / (stem + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ProgressDiagnostic = load_module("progress_guard").ProgressDiagnostic
Parser = load_module("terminus_json_plain_parser").TerminusJSONPlainParser


def test_disabled_mode_inert():
    x = ProgressDiagnostic()
    for _ in range(3):
        assert x.observe(["false"], "fatal: invalid option") is None
    assert x.summary()["turns"] == 0


def test_same_command_same_failure_is_counted_in_observe():
    x = ProgressDiagnostic("observe")
    x.observe(["badcmd"], "fatal: missing file")
    assert x.observe(["badcmd"], "fatal: missing file") is None
    assert x.summary()["same_command_same_error_repeats"] == 1
    assert x.summary()["feedback_events"] == 0


def test_feedback_does_not_rewrite_commands():
    x = ProgressDiagnostic("feedback")
    cmds = ["badcmd"]
    x.observe(cmds, "error: missing")
    note = x.observe(cmds, "error: missing")
    assert "advisory" in note and cmds == ["badcmd"]


def test_different_errors_do_not_trigger():
    x = ProgressDiagnostic("feedback")
    x.observe(["badcmd"], "error: missing alpha")
    assert x.observe(["badcmd"], "error: missing beta") is None


def test_success_and_polling_are_not_flagged():
    x = ProgressDiagnostic("feedback")
    for _ in range(3):
        x.observe(["git status"], "On branch main; clean")
    assert x.summary()["same_command_same_error_repeats"] == 0


def test_multiple_commands_not_falsely_attributed():
    x = ProgressDiagnostic("feedback")
    for _ in range(2):
        x.observe(["badcmd1", "badcmd2"], "fatal: unknown")
    assert x.summary()["same_command_same_error_repeats"] == 0


def test_parser_error_resets_chain():
    x = ProgressDiagnostic("feedback")
    x.observe(["badcmd"], "fatal: no such file")
    x.record_parser(error=True, warning=True)
    assert x.observe(["badcmd"], "fatal: no such file") is None
    assert x.summary()["parser_errors"] == 1


def test_no_plaintext_secrets_in_metrics():
    x = ProgressDiagnostic("observe")
    x.observe(["hidden-secret-123"], "fatal: hidden-secret-123")
    assert "hidden-secret-123" not in repr(x.__dict__)


def test_unknown_mode_fails_closed():
    with pytest.raises(ValueError):
        ProgressDiagnostic("task_solutions")


def test_parser_accepts_valid_command():
    r = Parser().parse_response('{"analysis":"inspect","plan":"run","commands":[{"keystrokes":"pwd\\n","duration":1}]}')
    assert not r.error and r.commands[0].keystrokes == "pwd\n"


def test_extra_text_is_warning_but_not_parse_failure():
    r = Parser().parse_response('Prefix: {"analysis":"inspect","plan":"run","commands":[{"keystrokes":"pwd"}]}')
    assert not r.error and len(r.commands) == 1 and "Extra text" in r.warning


def test_missing_command_structure_fails_closed():
    r = Parser().parse_response('{"analysis":"a","plan":"p"}')
    assert r.error and not r.commands


def test_command_keystrokes_wrong_type_rejected():
    r = Parser().parse_response('{"analysis":"a","plan":"p","commands":[{"keystrokes":42}]}')
    assert r.error and not r.commands


def test_missing_duration_is_tolerated_with_warning():
    r = Parser().parse_response('{"analysis":"a","plan":"p","commands":[{"keystrokes":"pwd"}]}')
    assert not r.error and r.commands[0].duration == 1 and "Missing duration" in r.warning
