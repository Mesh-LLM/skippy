#!/usr/bin/env python3
"""Derive bounded follow-up observations from permitted saved responses/tables."""
import hashlib
import json
from pathlib import Path

ROOT = Path('target/kv-profiling')
OUT = Path('evals/kv-profiling')


def load(path):
    return json.loads(path.read_text())


def source(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def tokens(response):
    value = response['choices'][0]['logprobs']
    if 'content' in value:
        return [{'token': row['token'], 'logprob': row['logprob'],
                 'top': {item['token']: item['logprob'] for item in row['top_logprobs']}}
                for row in value['content']]
    return [{'token': token, 'logprob': lp, 'top': top} for token, lp, top in
            zip(value['tokens'], value['token_logprobs'], value['top_logprobs'])]


def cold_summary():
    root = ROOT / 'followup-cold'
    controls = load(root / 'cold-controls.json')
    seq_paths = {'llama': ROOT / 'followup-correctness/llama-base-r0-logprobs.json',
                 'skippy': ROOT / 'followup-logprob-repair/skippy-base-r0-logprobs.json'}
    seq = {key: tokens(load(path)) for key, path in seq_paths.items()}
    rows = []
    evidence = [source(root / 'cold-controls.json')] + [source(path) for path in seq_paths.values()]
    for position in (0, 17, 59, 74, 75, 127, 255):
        row = {'reference_generated_tokens_in_prompt': position, 'engines': {}}
        for engine in ('llama', 'skippy'):
            paths = [root / f'{engine}-r{r}-p{position}.json' for r in (0, 1)]
            a, b = [tokens(load(path)) for path in paths]
            evidence.extend(source(path) for path in paths)
            item = {'cold': a[0], 'fresh_process_aa_exact': a == b,
                    'reference_token_ids_exact': all(x['token_ids_exact'] for case in controls['cases']
                         if case['engine'] == engine for x in case['positions'] if x['position'] == position)}
            if engine == 'llama' or position <= 74:
                item['original_decode_at_same_token_context'] = seq[engine][position]
                item['cold_decode_top1_equal'] = a[0]['token'] == seq[engine][position]['token']
                shared = a[0]['token']
                if shared in seq[engine][position]['top']:
                    item['cold_minus_decode_chosen_logprob'] = a[0]['logprob'] - seq[engine][position]['top'][shared]
            else:
                item['decode_comparison'] = 'NOT APPLICABLE: original Skippy trajectory diverged earlier; reference prefix differs'
            row['engines'][engine] = item
        row['cross_engine_cold_top1_equal'] = row['engines']['llama']['cold']['token'] == row['engines']['skippy']['cold']['token']
        rows.append(row)
    result = {'scope': 'Seven exact-token reference prefixes, two fresh lifetimes per engine. Cold-prefill one-token scores versus original greedy decode only at equal contexts. Not a full teacher-forced decode or quality proof.',
              'rows': rows, 'evidence': evidence,
              'verdict': 'Observed deterministic prefill/decode path dependence within vanilla at token74; both cold engines choose keeper. Cross-engine cold top1 agreement7/7 does not establish numerical equivalence or correctness. Root operator/state cause UNPROVEN.'}
    (OUT / 'followup-cold-summary.json').write_text(json.dumps(result, indent=2) + '\n')


def operator_summary():
    path = ROOT / 'followup-attribution/operator-attribution.json'
    data = load(path)
    cases = []
    for case in data['cases']:
        result = {key: case[key] for key in ('label', 'model', 'mode', 'ordinary', 'command_rows',
                  'incomplete_graph_command_sets', 'dropped_counter_observations')}
        result['requests'] = []
        for req in case['requests']:
            phases = {}
            for name, phase in req['phases'].items():
                wall = phase['wall_ns']
                phases[name] = {'wall_ms': wall / 1e6, 'gpu_active_union_ms': phase['gpu_active_union_ns'] / 1e6,
                                'gpu_union_pct_of_phase': phase['gpu_active_union_ns'] / wall * 100,
                                'class_union_ms': {k: v / 1e6 for k, v in phase['per_class_union_ns'].items()},
                                'command_counts_per_compute': phase['command_counts_per_compute'],
                                'attention_row_nodes_and_kv_cells': phase['attention_row_nodes_and_kv_cells']}
            result['requests'].append({'bucket': req['bucket'], 'usage': req['usage'], 'phases': phases})
        cases.append(result)
    result = {'scope': 'Existing native callback GPU timestamp interval unions clipped to HTTP event phases. Class splitting serializes/fuses differently.16generated tokens, not full timing work. Classes are op groups, not shader/kernel attribution. CPU summed sample weights are not wall time.',
              'evidence': source(path), 'cases': cases,
              'unsupported': 'gpucapture/gpudebug/metalperftrace unavailable on target; shader/counter attribution NOT RUN. No memory-bound or ceiling-percent claim from these intervals.'}
    (OUT / 'followup-operator-summary.json').write_text(json.dumps(result, indent=2) + '\n')


def chunk_summary():
    import re
    root = ROOT / 'followup-chunk'
    record = load(root / 'chunk-controls.json')
    rows = []
    observations = {}
    for engine in ('llama', 'skippy'):
        saved = {}
        aa = []
        for repeat in (0, 1):
            label = f'{engine}-r{repeat}'
            paths = [root / f'{label}-{mode}.json' for mode in ('normal', 'normal-repeat', 'logprobs')]
            responses = [load(path) for path in paths]
            if any((r['usage']['prompt_tokens'], r['usage']['completion_tokens']) != (512, 256)
                   or r['usage'].get('prompt_tokens_details', {}).get('cached_tokens', 0) for r in responses):
                raise ValueError('chunk diagnostic usage/cache gate failed')
            text = responses[0]['choices'][0]['text']
            if any(r['choices'][0]['text'] != text for r in responses):
                raise ValueError('chunk diagnostic normal/repeat/logprob text differs')
            saved[repeat] = {'text': text, 'tokens': tokens(responses[2])}
            aa.extend(source(path) for path in paths)
            path = root / f'{label}-server.log'
            messages = []
            for line in path.read_text().splitlines():
                try:
                    messages.append(json.loads(line).get('data', {}).get('message', ''))
                except ValueError:
                    messages.append(line)
            log = '\n'.join(messages)
            params = {name: sorted(set(re.findall(r'llama_context:\s+' + name + r'\s+=\s+(\w+)', log)))
                      for name in ('n_ctx', 'n_batch', 'n_ubatch', 'n_seq_max', 'n_rs_seq')}
            if params['n_ubatch'] != ['1']:
                raise ValueError('single-token microbatch was not actually resolved')
            rows.append({'engine': engine, 'repeat': repeat, 'native_context': params,
                         'native_log': source(path), 'normal_repeat_logprob_text_exact': True})
        observations[engine] = saved[0]
        original = ROOT / ('followup-correctness/llama-base-r0-logprobs.json' if engine == 'llama'
                           else 'followup-logprob-repair/skippy-base-r0-logprobs.json')
        rows.append({'engine': engine, 'fresh_process_text_aa_exact': saved[0]['text'] == saved[1]['text'],
                     'fresh_process_logprob_aa_exact': saved[0]['tokens'] == saved[1]['tokens'],
                     'text_equals_original_microbatch512': saved[0]['text'] == load(original)['choices'][0]['text'],
                     'logprob_arrays_equal_original_microbatch512': saved[0]['tokens'] == tokens(load(original)),
                     'response_evidence': aa, 'original_reference': source(original)})
    a, b = [observations[e]['tokens'] for e in ('llama', 'skippy')]
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x['token'] != y['token']), None)
    result = {'scope': record['scope'], 'rows': rows, 'cross_engine_text_exact': observations['llama']['text'] == observations['skippy']['text'],
              'cross_engine_logprob_arrays_exact': a == b, 'first_cross_engine_different_token': first,
              'chosen_logprob_abs_difference': {'mean': sum(abs(x['logprob']-y['logprob']) for x,y in zip(a,b))/len(a),
                  'max': max(abs(x['logprob']-y['logprob']) for x,y in zip(a,b)),
                  'scope': 'Observed chosen-token logprob difference only; no full-vocabulary KL or acceptance tolerance.'},
              'token74': {e: observations[e]['tokens'][74] for e in ('llama', 'skippy')},
              'limitation': 'Batch-geometry diagnostic, not a second speed candidate or a high-precision operator oracle. Root operator/state cause and general quality remain UNPROVEN.',
              'control_receipt': source(root / 'chunk-controls.json')}
    (OUT / 'followup-chunk-summary.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    cold_summary()
    operator_summary()
    if (ROOT / 'followup-chunk/chunk-controls.json').exists():
        chunk_summary()
