"""Matched-run diagnostics only; this module is not part of the upgrade PR."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from . import inference_probe
from .ci_process import stop


def require_cached(model):
    """Reject missing fixtures before admission can request a download."""
    for row in model['files']:
        roots = [Path.home() / '.cache/skippy/inference-models' / row['sha256']]
        if os.environ.get('HF_HUB_CACHE'):
            roots.append(Path(os.environ['HF_HUB_CACHE']) /
                         ('models--' + model['repo'].replace('/', '--')) /
                         'snapshots' / model['revision'] / row['path'])
        if not any(path.is_file() and path.stat().st_size == row['size'] for path in roots):
            raise FileNotFoundError('required cached fixture missing: ' + row['path'])
    os.environ['HF_HUB_OFFLINE'] = '1'


@contextmanager
def capture():
    output = Path(os.environ['JIANYANG_ARTIFACT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    original_send = inference_probe.send
    first = True

    def send(request, sample):
        nonlocal first
        if first:
            print('Prefill diagnostic: settling for 30 seconds before the warm probe', flush=True)
            time.sleep(30)
            first = False
        sample['request_started_unix_ns'] = time.time_ns()
        try:
            return original_send(request, sample)
        finally:
            sample['request_ended_unix_ns'] = time.time_ns()

    query = ['nvidia-smi', '--query-gpu=timestamp,name,pstate,temperature.gpu,clocks.sm,'
             'clocks.mem,utilization.gpu,utilization.memory,power.draw',
             '--format=csv,noheader,nounits', '--loop-ms=200']
    with (output / 'gpu-telemetry.csv').open('wb') as stream, \
            (output / 'gpu-telemetry.stderr').open('wb') as errors:
        process = subprocess.Popen(query, stdout=stream, stderr=errors, start_new_session=True)
        inference_probe.send = send
        try:
            time.sleep(0.5)
            if process.poll() is not None:
                raise RuntimeError('GPU telemetry query failed; inspect gpu-telemetry.stderr')
            yield
        finally:
            inference_probe.send = original_send
            stop(process)
            path = output / 'server.log'
            lines = path.read_text(errors='replace').splitlines() if path.exists() else []
            keys = ('n_rs_seq', 'rs_replay', 'n_ctx', 'n_batch', 'n_ubatch',
                    'n_threads', 'flash_attn', 'offloaded', 'GPU name')
            detail = {'source': os.environ['JIANYANG_COMMIT'], 'settle_seconds': 30,
                      'diagnostic_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'probe_sha256': hashlib.sha256(Path(inference_probe.__file__).read_bytes()).hexdigest(),
                      'native_settings': [line for line in lines if any(key in line for key in keys)],
                      'telemetry_query': query, 'telemetry_bytes': (output/'gpu-telemetry.csv').stat().st_size}
            # JSON is retained by the native artifact collector alongside the raw CSV.
            detail['telemetry_csv'] = (output / 'gpu-telemetry.csv').read_text(errors='replace')
            (output / 'prefill-diagnostics.json').write_text(json.dumps(detail, indent=2))
