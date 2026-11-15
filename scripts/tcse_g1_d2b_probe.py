"""D2B: same four synthetic D2 cases, JSON-mode transport diagnostic."""
import json
import hashlib
import statistics
import time
from pathlib import Path
from tcse_g1_d2_probe import samples, percentile


def run():
    from litellm import completion
    import importlib.util
    import sys
    source = Path(__file__).resolve().parents[1] / 'third_party/harbor_terminus2/terminus_json_plain_parser.py'
    spec = importlib.util.spec_from_file_location('tcse_d2b_original_parser', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    parser = module.TerminusJSONPlainParser()
    rows = []
    for case, prompt in samples():
        started = time.monotonic()
        try:
            result = completion(
                model='ollama_chat/qwen2.5-coder:1.5b',
                api_base='http://127.0.0.1:11434',
                messages=[{'role': 'user', 'content': prompt}],
                response_format={'type': 'json_object'},
                max_tokens=350,
                timeout=180,
            )
            raw = result.choices[0].message.content or ''
            parsed = parser.parse_response(raw)
            try:
                strict = isinstance(json.loads(raw), dict)
            except (ValueError, TypeError):
                strict = False
            commands = [c.keystrokes for c in parsed.commands]
            rows.append({
                'case':case,'sec':round(time.monotonic()-started,3),
                'strict_json':strict,'parser_ok':not bool(parsed.error),
                'parser_warning':bool(parsed.warning),'n_commands':len(commands),
                'repeat_failed_dummy':case=='error' and any(
                    'cd /impossible/synthetic/path' in c for c in commands),
                'response_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                'error_type':None,
            })
        except Exception as exc:
            rows.append({
                'case':case,'sec':round(time.monotonic()-started,3),
                'strict_json':False,'parser_ok':False,'parser_warning':False,
                'n_commands':0,'repeat_failed_dummy':None,'response_sha256':None,
                'error_type':type(exc).__name__,
            })
        print(case, rows[-1]['sec'], rows[-1]['strict_json'],
              rows[-1]['parser_ok'], rows[-1]['error_type'], flush=True)
    sec = [r['sec'] for r in rows]
    p90 = round(percentile(sec, .9),3)
    success = (p90 <= 35 and all(r['strict_json'] and r['parser_ok']
              and r['n_commands']==1 and not r['repeat_failed_dummy']
              and r['error_type'] is None for r in rows))
    report = {
        'phase':'D2B_JSON_MODE','comparison_run':43760298933,
        'baseline_strict':0,'baseline_one_command_valid':1,
        'status':'D2B_GO_FOR_HARNESS_DESIGN_REVIEW' if success else 'D2B_STOP_AND_DIAGNOSE',
        'strict_json':sum(r['strict_json'] for r in rows),
        'one_command_valid':sum(r['parser_ok'] and r['n_commands']==1 for r in rows),
        'p50_sec':round(statistics.median(sec),3),'p90_sec':p90,
        'model':'ollama_chat/qwen2.5-coder:1.5b',
        'transport':'LiteLLM response_format=json_object',
        'no_benchmark_tasks_executed':True,'cases':rows,
    }
    path=Path('tcse_d2b_receipts/result.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))


if __name__ == '__main__':
    run()
