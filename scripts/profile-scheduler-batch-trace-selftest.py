#!/usr/bin/env python3
"""Synthetic audit checks only; never native qualification evidence."""
import copy
import json
from pathlib import Path
import runpy
import shutil
import tempfile


def main():
    history = Path('target/scheduler-fixes/artifacts/qwen35-batch-history')
    prior = Path('target/scheduler-four-models/artifacts/qwen35-c128-stream-controls')
    loaded = Path('target/scheduler-four-models/artifacts/qwen35-c128-retry')
    old_audit = runpy.run_path('scripts/profile-scheduler-batch-history-audit.py')['audit']
    assert old_audit(history, prior, loaded)['responses_verified'] == 272
    module = runpy.run_path('scripts/profile-scheduler-batch-trace-audit.py')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        record = json.loads((history/'batch-history.json').read_text())
        record['trace_baseline'] = True
        record['cells'] = [c for c in record['cells'] if c['arm'] == 'skippy-base']
        (root/'batch-history.json').write_text(json.dumps(record))
        for cell in record['cells']:
            name = f"c{cell['first']}-first-skippy-base"
            directory = root/'evidence'/name
            shutil.copytree(history/'evidence'/name, directory)
            launch = json.loads((directory/'launch.json').read_text())
            launch['argv'] += ['--telemetry-level', 'debug', '--telemetry-queue-capacity', '65536']
            launch['environment_names'] = sorted(launch['environment_names'] + ['SKIPPY_TELEMETRY_STDERR'])
            (directory/'launch.json').write_text(json.dumps(launch))
            with (directory/'server.log').open('a') as stream:
                for item in cell['waves']:
                    wave = json.loads((directory/item['file']).read_text())
                    size = item['concurrency']
                    at = wave['start_unix_ns'] + min(r['send_ms'] for r in wave['requests'].values()) * 1e6
                    event = lambda kind, attrs: json.dumps({'event': kind, 'start_time_unix_nanos': at,
                        'end_time_unix_nanos': at + 1, 'attributes': attrs}) + '\n'
                    for _ in range(size):
                        stream.write(event('stage.scheduler_route', {'skippy.scheduler.generation_driver': 'built-in'}))
                        stream.write(event('stage.scheduler_iteration', {'skippy.scheduler.prefill_tokens': 512,
                            'skippy.scheduler.decode_tokens': 0, 'skippy.scheduler.running': size}))
                    for _ in range(31):
                        stream.write(event('stage.scheduler_iteration', {'skippy.scheduler.prefill_tokens': 0,
                            'skippy.scheduler.decode_tokens': size, 'skippy.scheduler.running': size}))
                    stream.write(event('counter', {'skippy.otel_export_errors': 0}))
        result = module['audit'](root, prior, loaded, history)
        assert result['responses_verified'] == 68 and len(result['scheduler_traces']) == 8
        directory = root/'evidence/c1-first-skippy-base'
        lines = (directory/'server.log').read_text().splitlines()
        wave = json.loads((directory/'0-c1.json').read_text())
        for changed in (lines + [json.dumps({'attributes': {'skippy.otel_export_errors': 1}})],
                        [line for line in lines if 'stage.scheduler_iteration' not in line]):
            try:
                module['wave_trace'](changed, wave, 1)
            except ValueError:
                pass
            else:
                raise AssertionError('mutated trace accepted')
        request_path = directory/'0-requests.json'
        request = json.loads(request_path.read_text())
        request['subject-00']['cache_prompt'] = True
        request_path.write_text(json.dumps(request))
        try:
            module['audit'](root, prior, loaded, history)
        except ValueError:
            pass
        else:
            raise AssertionError('changed request accepted')
    print('PASS archived272; synthetic68/eight waves; dropped/missing trace and changed request rejected. Not native evidence.')


if __name__ == '__main__':
    main()
