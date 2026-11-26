#!/usr/bin/env python3
"""Audit scheduler step evidence without attributing a native operator fault."""
import argparse
from collections import Counter
import json
from pathlib import Path
import runpy


def require(value, message):
    if not value:
        raise ValueError(message)


def wave_trace(lines, wave, size):
    start = wave['start_unix_ns']
    end = start + max(r['end_ms'] for r in wave['requests'].values()) * 1e6
    steps, routes, counters = [], [], 0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        attrs = row.get('attributes', {})
        for key, value in attrs.items():
            if ('drop' in key or key == 'skippy.otel_export_errors') and isinstance(value, (int, float)):
                counters += 1
                require(value == 0, 'telemetry loss/export error')
        begin, finish = row.get('start_time_unix_nanos'), row.get('end_time_unix_nanos')
        if begin is None or finish is None or not (finish >= start and begin <= end):
            continue
        if row.get('event') == 'stage.scheduler_route':
            routes.append(attrs.get('skippy.scheduler.generation_driver'))
        if row.get('event') != 'stage.scheduler_iteration':
            continue
        selected = {k.removeprefix('skippy.scheduler.'): v for k, v in attrs.items()
                    if k.startswith('skippy.scheduler.') and not k.endswith('_ms')}
        if selected.get('prefill_tokens', 0) or selected.get('decode_tokens', 0):
            steps.append(selected)
    require(counters > 0, 'missing telemetry counters')
    require(routes == ['built-in'] * size, 'unexpected/missing request routes')
    require(steps, 'missing scheduler iterations')
    require(sum(s['prefill_tokens'] for s in steps) == 512 * size, 'prefill token accounting')
    require(sum(s['decode_tokens'] for s in steps) == 31 * size, 'decode token accounting')
    shapes = Counter((s['prefill_tokens'], s['decode_tokens'], s['running']) for s in steps)
    return {'request_count': size, 'counter_observations': counters, 'steps': steps,
            'shapes': [{'prefill_tokens': p, 'decode_tokens': d, 'running': r, 'steps': count}
                       for (p, d, r), count in sorted(shapes.items())]}


def audit(root, prior, loaded, history):
    read = lambda p: json.loads(p.read_text())
    record, old = read(root/'batch-history.json'), read(history/'batch-history.json')
    require(record.get('trace_baseline') is True and old['complete'], 'invalid trace/history')
    for field in ('identity', 'candidate', 'model', 'fixture_sha256', 'prompt'):
        require(record[field] == old[field], 'trace/history identity drift')
    result = runpy.run_path(str(Path(__file__).with_name('profile-scheduler-batch-history-audit.py')))['audit'](
        root, prior, loaded, history)
    waves = []
    for cell in record['cells']:
        directory = root/'evidence'/f"c{cell['first']}-first-{cell['arm']}"
        lines = (directory/'server.log').read_text().splitlines()
        for item in cell['waves']:
            waves.append({'first': cell['first'], 'index': item['index'],
                          **wave_trace(lines, read(directory/item['file']), item['concurrency'])})
    result['scheduler_traces'] = waves
    result['scope'] = ('Unchanged baseline scheduler-step trace with exact archived output calibration. '
                       'Step counts are serving telemetry, not independent native kernel/row attribution; '
                       'no runtime fix, performance claim, or original c128/cross-engine clearance.')
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('artifacts', 'prior', 'loaded', 'history'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    result = audit(a.artifacts, a.prior, a.loaded, a.history)
    (a.artifacts/'audit.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'responses_verified': result['responses_verified'],
                      'waves': len(result['scheduler_traces']), 'controls': result['controls']}))
