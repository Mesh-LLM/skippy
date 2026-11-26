#!/usr/bin/env python3
"""Audit the preregistered single-flag screen and fresh paired observations."""
import argparse
import hashlib
import json
import math
import random
import re
import statistics
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def signature(response):
    return response['choices'][0]


def audit_quality(artifacts, record):
    rows = []
    for row in record['quality']:
        model = row['model']
        responses = {arm: read(artifacts / f'{model}-{arm}-outputs.json') for arm in ('base', 'ordinary')}
        probs = {arm: read(artifacts / f'{model}-{arm}-logprobs.json') for arm in ('base', 'ordinary')}
        if any(len(value) != 4 for value in responses.values()):
            raise ValueError('quality prompt coverage incomplete')
        for arm in responses:
            for i, response in enumerate(responses[arm] + [probs[arm]]):
                expected = (512, 256) if i in (0, 1, 4) else (4096, 64)
                usage = response['usage']
                if (usage['prompt_tokens'], usage['completion_tokens']) != expected:
                    raise ValueError('quality usage differs from registered work')
                if usage.get('prompt_tokens_details', {}).get('cached_tokens', 0):
                    raise ValueError('quality prompt cache unexpectedly reused')
            if signature(probs[arm])['text'] != signature(responses[arm][1])['text']:
                raise ValueError('normal versus logprob output differs')
        text_equal = all(signature(a)['text'] == signature(b)['text'] for a, b in zip(responses['base'], responses['ordinary']))
        probs_equal = signature(probs['base'])['logprobs'] == signature(probs['ordinary'])['logprobs']
        if text_equal != row['four_texts_equal'] or probs_equal != row['logprobs_exact']:
            raise ValueError('independent quality audit disagrees with runner')
        rows.append({'model': model, 'four_texts_exact': text_equal, 'logprob_arrays_exact': probs_equal,
                     'normal_logprob_text_equal': True, 'counts_cache_pass': True})
    return rows


def audit_timing(root, record, source_sha, expected_pairs=6, checks_only=False):
    model = root / 'qwen3.5-35b-a3b-q4-k-m'
    allowed_env = {'PATH', 'HOME', 'TMPDIR', 'LANG', 'HF_HUB_OFFLINE', 'HF_DATASETS_OFFLINE', 'INFERENCEKIT_RUNS_DIR'}
    settings = {'--ctx-size': '8192', '--generation-concurrency': '1', '--n-gpu-layers': '999',
                '--speculative-strategy': 'disabled', '--prefix-cache': 'off', '--cache-idle-slots': '0',
                '--kv-cache-type-k': 'f16', '--kv-cache-type-v': 'f16', '--flash-attention': 'enabled',
                '--batch-size': '512', '--microbatch-size': '512', '--threads': '16', '--threads-batch': '16'}
    cells, counts, launches, evidence, swaps = {}, {}, {}, [], []
    for entry in record['timing']:
        pair, position, arm = entry['pair'], entry['position'], entry['arm']
        order = ('base', 'ordinary') if pair % 2 == 0 else ('ordinary', 'base')
        if arm != order[position] or pair not in range(6) or position not in (0, 1):
            raise ValueError('unregistered pair order')
        cell = model / f'b{pair}-p{position}-{arm}'
        launch = read(cell / 'launch.json')
        argv = launch['argv']
        if set(launch['environment_names']) != allowed_env:
            raise ValueError('unexpected environment')
        if any(flag not in argv or argv[argv.index(flag) + 1] != value for flag, value in settings.items()):
            raise ValueError('serving controls changed')
        if ('--kv-row-attention=false' in argv) != (arm == 'ordinary'):
            raise ValueError('candidate flag differs')
        before, after = [read(cell / f'{b}.json') for b in ('before', 'after')]
        for boundary in (before, after):
            if boundary['native_thermal_pressure_level']:
                raise ValueError('thermal gate failed')
            swaps.append(float(re.search(r'used = ([0-9.]+)M', boundary['sysctl vm.swapusage'])[1]))
        if (cell / 'contention.json').exists() or (cell / 'thermal-failure.json').exists():
            raise ValueError('invalid host conditions')
        bench = read(cell / 'bench.json')
        if bench['evidence_id'] != entry['evidence_id'] or bench['identity']['engine']['version'] != source_sha:
            raise ValueError('evidence/source identity changed')
        if bench['provenance']['tool_version'] != '0.1.1' or not bench['coverage']['complete']:
            raise ValueError('client identity or coverage changed')
        request = bench['data']['request']['args']
        expected_extra = {'seed': 42, 'top_p': 1, 'top_k': 0, 'min_p': 0, 'repeat_penalty': 1, 'cache_prompt': False}
        if request['warmup'] != 2 or not request['exact_length'] or json.loads(request['extra_body']) != expected_extra:
            raise ValueError('request controls changed')
        path = root / 'evidence' / bench['evidence_id'] / 'requests.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows = [row for row in rows if row['round'] == pair]
        expected_ids = {'p512-0', 'p512-1', 'p4096-0', 'p4096-1'}
        salt = f'ik-r{pair}' if pair else None
        if len(rows) != 4 or {row['id'] for row in rows} != expected_ids:
            raise ValueError('current-round requests missing/duplicated')
        if any(not r['ok'] or r.get('count_mismatch') or r['n_tokens'] != (256 if r['bucket'] == 'p512' else 64)
               or r['salt'] != salt for r in rows):
            raise ValueError('request success/count/salt gate failed')
        token_rows = read(cell / 'prompt-token-counts.json')['rows']
        if len(token_rows) != 4 or {r['id'] for r in token_rows} != expected_ids or any(
                r['round'] != pair or r['salt'] != salt or r['prompt_tokens'] !=
                (512 if r['id'].startswith('p512') else 4096) + (3 if pair else 0) for r in token_rows):
            raise ValueError('native tagged prompt counts differ')
        if (pair, arm) in cells:
            raise ValueError('duplicate paired cell')
        cells[pair, arm] = rows
        counts[pair, arm] = token_rows
        launches[pair, arm] = [value for value in argv if value != '--kv-row-attention=false']
        evidence.append({'cell': cell.name, 'evidence_id': bench['evidence_id'], 'current_round_requests': len(rows),
                         'requests_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if set(cells) != {(p, a) for p in range(expected_pairs) for a in ('base', 'ordinary')}:
        raise ValueError('six fresh pairs required')
    for pair in range(expected_pairs):
        if counts[pair, 'base'] != counts[pair, 'ordinary']:
            raise ValueError('paired token identities differ')
        if launches[pair, 'base'] != launches[pair, 'ordinary']:
            raise ValueError('paired serving arguments differ beyond the single candidate flag')
    if checks_only:
        return {'complete_pairs_checked': expected_pairs, 'count_host_identity_gates_pass': True}
    metrics = []
    for bucket in ('p512', 'p4096'):
        for metric in ('e2e_ms', 'ttft_ms', 'tpot_ms'):
            medians = {arm: [statistics.median(r[metric] for r in cells[p, arm] if r['bucket'] == bucket)
                             for p in range(6)] for arm in ('base', 'ordinary')}
            ratios = [b / a for a, b in zip(medians['base'], medians['ordinary'])]
            def geometric(values):
                return math.exp(statistics.mean(math.log(v) for v in values))
            rng = random.Random(42)
            draws = sorted(geometric(rng.choices(ratios, k=6)) for _ in range(10000))
            metrics.append({'bucket': bucket, 'metric': metric, 'paired_cell_medians': medians,
                            'ordinary_over_base_ratios': ratios, 'geometric_mean_ratio': geometric(ratios),
                            'ci95_percentile': [draws[249], draws[9749]]})
    primary = next(row for row in metrics if row['bucket'] == 'p4096' and row['metric'] == 'e2e_ms')
    secondary = [row for row in metrics if row is not primary and
                 (row['metric'] in ('e2e_ms', 'ttft_ms') or row['bucket'] == 'p512')]
    retained = primary['ci95_percentile'][1] < .98 and all(row['ci95_percentile'][1] <= 1.02 for row in secondary)
    return {'timed_requests': 48, 'paired_units': 6, 'metrics': metrics, 'cells': evidence,
            'swap_used_mib_values': sorted(set(swaps)), 'meets_registered_retention_rule': retained,
            'verdict': 'KEEP FOR FURTHER QUALIFICATION' if retained else 'DO NOT PROMOTE: registered performance/secondary gates not met',
            'scope': 'Descriptive within-Skippy experiment. Small exact screen is not general task quality; upstream equivalence remains independently failed.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--extracted-root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    record = read(args.artifacts / 'candidate.json')
    quality = audit_quality(args.artifacts, record)
    result = {'quality': quality, 'runner_verdict': record.get('verdict'), 'candidate_private_root': record['private_root'],
              'artifact_sha256': hashlib.sha256((args.artifacts / 'candidate.json').read_bytes()).hexdigest()}
    if record['timing']:
        if len(quality) != 4 or any(not row['four_texts_exact'] or not row['logprob_arrays_exact'] for row in quality):
            raise ValueError('timing despite failed/incomplete quality gate')
        if args.extracted_root is None:
            raise ValueError('timing audit requires extracted synthetic evidence')
        result['timing'] = audit_timing(args.extracted_root, record, read(Path('evals/kv-profiling/campaign.json'))['source_sha'])
    else:
        result['timing'] = {'verdict': 'NOT RUN', 'reason': record.get('verdict', 'runner incomplete')}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'timing'}, indent=2))
    print('TIMING', result['timing']['verdict'])


if __name__ == '__main__':
    main()
