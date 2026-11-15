"""D2 bounded synthetic-only model format/latency probe. No benchmark tasks."""
import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path


def percentile(values, p):
    sorted_v = sorted(values)
    x = (len(sorted_v) - 1) * p
    i = int(x)
    return sorted_v[i] + (sorted_v[min(i + 1, len(sorted_v) - 1)] - sorted_v[i]) * (x - i)


def samples():
    instruction = (
        'SYNTHETIC FORMAT TEST ONLY, not a real coding benchmark. '
        'Respond as a single JSON object, without markdown or other text. '
        'Required: "analysis" string, "plan" string, '
        '"commands" array of one {"keystrokes":string,"duration":number} object; '
        '"task_complete": false. No external network access.'
    )
    return [
        ('short', instruction + ' Give a harmless command to show the working directory.'),
        ('error', instruction + " A past command 'cd /impossible/synthetic/path' failed with "
         "'No such file or directory'. Choose a different useful diagnostic command."),
        ('long', instruction + ' Background: completely fictional sandbox, no secrets. ' * 75
         + ' Give a harmless command to list current files.'),
        ('repeat', instruction + ' Give a harmless command to print SYNTHETIC_OK.'),
    ]


def probe(model, url, dest, latency_gate):
    from litellm import completion
    from harbor_terminus2.terminus_json_plain_parser import TerminusJSONPlainParser
    parser = TerminusJSONPlainParser()
    rows = []
    for name, prompt in samples():
        start = time.monotonic()
        try:
            response = completion(model=model, api_base=url,
                                  messages=[{'role': 'user', 'content': prompt}],
                                  max_tokens=350, timeout=180)
            content = response.choices[0].message.content or ''
            parsed = parser.parse_response(content)
            try:
                strict = isinstance(json.loads(content), dict)
            except (ValueError, TypeError):
                strict = False
            rows.append(dict(case=name, seconds=round(time.monotonic() - start, 3),
                             strict_json=strict, tolerant_parse=not bool(parsed.error),
                             parser_warning=bool(parsed.warning),
                             command_count=len(parsed.commands),
                             repeated_failed_command=(name == 'error' and any(
                                 'cd /impossible/synthetic/path' in cmd.keystrokes
                                 for cmd in parsed.commands)),
                             response_sha256=hashlib.sha256(content.encode()).hexdigest(),
                             error_type=None))
        except Exception as exc:
            rows.append(dict(case=name, seconds=round(time.monotonic() - start, 3),
                             strict_json=False, tolerant_parse=False,
                             parser_warning=False, command_count=0,
                             repeated_failed_command=None, response_sha256=None,
                             error_type=type(exc).__name__))
        print(name, rows[-1]['seconds'], 'seconds',
              'parse', rows[-1]['tolerant_parse'], flush=True)
    p90 = round(percentile([r['seconds'] for r in rows], .9), 3)
    no_repeat = not next(r['repeated_failed_command'] for r in rows if r['case'] == 'error')
    acceptable = p90 <= latency_gate and no_repeat and all(
        r['tolerant_parse'] and r['command_count'] == 1
        and r['error_type'] is None for r in rows
    )
    out = dict(phase='D2_SYNTHETIC_ONLY', model=model,
               decision='D2_GO_TO_D3_PLANNING' if acceptable else 'D2_STOP_AND_DIAGNOSE',
               strict_json_count=sum(r['strict_json'] for r in rows),
               valid_command_count=sum(r['tolerant_parse'] and r['command_count'] == 1 for r in rows),
               p50_seconds=round(statistics.median(r['seconds'] for r in rows), 3),
               p90_seconds=p90, p90_gate_seconds=latency_gate,
               repeated_failed_synthetic_command=not no_repeat,
               no_benchmark_tasks_executed=True, cases=rows)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({k:v for k,v in out.items() if k != 'cases'}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--model', default='ollama_chat/qwen2.5-coder:1.5b')
    p.add_argument('--url', default='http://127.0.0.1:11434')
    p.add_argument('--latency-gate', type=float, default=35)
    p.add_argument('--output', type=Path, default=Path('tcse_d2_receipts/result.json'))
    a = p.parse_args()
    probe(a.model, a.url, a.output, a.latency_gate)
