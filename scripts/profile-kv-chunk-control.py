#!/usr/bin/env python3
"""Numerical isolation: single-token microbatches, not an optimization candidate."""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path

import lib.profile_kv_host as host


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = json.loads(args.fixture.read_text())
    model = next(m for m in json.loads((args.cache / 'models.json').read_text()) if m['key'] == fixture['model'])
    root = args.cache / ('followup-chunk-' + str(time.time_ns()))
    root.mkdir()
    env = host.clean_env(root)
    original = host.launch_args
    def launch(cache, selected, engine, lanes=1):
        argv = original(cache, selected, engine, lanes)
        flag = '--microbatch-size' if engine == 'skippy' else '-ub'
        argv[argv.index(flag) + 1] = '1'
        return argv
    host.launch_args = launch
    result = {'private_root': str(root), 'fixture_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
              'scope': 'Numerical path diagnostic only: microbatch1, otherwise baseline settings. Not a second speed candidate.', 'cases': []}
    try:
        for engine in ('llama', 'skippy'):
            for repeat in range(2):
                label = f'{engine}-r{repeat}'
                directory = root / label
                directory.mkdir()
                print('CHUNK_CONTROL', label, flush=True)
                time.sleep(30)
                host.require_idle(env)
                case = {'engine': engine, 'repeat': repeat, 'responses': []}
                with host.server(args.cache, model, engine, directory, env, diagnostic=True) as child:
                    for mode in ('normal', 'normal-repeat', 'logprobs'):
                        host.require_idle(env, child.pid)
                        request = host.body(model, fixture['prompt'], 256)
                        if mode == 'logprobs':
                            request['logprobs'] = 5
                        response = host.post('/v1/completions', request)
                        name = label + '-' + mode + '.json'
                        host.save(args.output / name, response)
                        case['responses'].append(name)
                        usage = response['usage']
                        if usage['prompt_tokens'] != 512 or usage['completion_tokens'] != 256:
                            raise ValueError('numerical control count mismatch')
                        if usage.get('prompt_tokens_details', {}).get('cached_tokens', 0):
                            raise ValueError('numerical control unexpectedly reused prompt')
                    if host.thermal_pressure():
                        raise host.HostUnavailable('thermal failure in numerical diagnostic')
                for name in ('launch.json', 'before.json', 'after.json', 'server.log'):
                    shutil.copy2(directory / name, args.output / (label + '-' + name))
                result['cases'].append(case)
                host.save(args.output / 'chunk-controls.json', result)
    finally:
        host.launch_args = original
        host.save(args.output / 'chunk-controls.json', result)


if __name__ == '__main__':
    main()
