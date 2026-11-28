const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '../actions/rocm-workspace/ownership.cjs'), 'utf8');

function ownership(env = {}, { uid = 1000, status = 0 } = {}) {
  const calls = [];
  runInNewContext(source, {
    process: { env: { GITHUB_WORKSPACE: '/__w/skippy/skippy', GITHUB_STATE: 'state', ...env }, getuid: () => 0 },
    console: { log() {} },
    require(name) {
      if (name === 'node:path') return require(name);
      if (name === 'node:fs') return {
        statSync: path => { assert.equal(path, '/__w'); return { uid, gid: 1000 }; },
        appendFileSync: (...args) => calls.push(args),
      };
      assert.equal(name, 'node:child_process');
      return { spawnSync: (command, args) => { calls.push([command, ...args]); return { status }; } };
    },
  });
  return calls;
}

test('ROCm cleanup restores the native workspace owner without following symlinks', () => {
  assert.deepEqual(ownership(), [['state', 'owner=1000:1000\n']]);
  assert.deepEqual(ownership({ STATE_owner: '1000:1000' }), [
    ['chown', '-R', '--no-dereference', '1000:1000', '/__w'],
  ]);
  assert.throws(() => ownership({ GITHUB_WORKSPACE: '/other/repo' }), /root job container/);
  assert.throws(() => ownership({}, { uid: 0 }), /Cannot identify/);
  assert.throws(() => ownership({ STATE_owner: '0:0' }), /Invalid native/);
  assert.throws(() => ownership({ STATE_owner: '1000:1000' }, { status: 1 }), /cleanup failed/);
});

test('ROCm cleanup runs after package cache posts on every outcome', () => {
  const action = readFileSync(join(__dirname, '../actions/rocm-workspace/action.yml'), 'utf8');
  assert.match(action, /post: ownership.cjs\n  post-if: always\(\)/);
  const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8')
    .split('\n  package_linux_x86_64_rocm:\n')[1].split(/\n  \w+:\n/)[0];
  assert.ok(workflow.indexOf('uses: ./.github/actions/rocm-workspace') <
            workflow.indexOf('uses: ./.github/actions/ci-package'));
  assert.match(workflow, /if: needs.plan.outputs.profile == 'full'/);
});

test('full CI and releases reuse one ROCm package job and the shared platform CLI', () => {
  const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
  const job = workflow.split('\n  package_linux_x86_64_rocm:\n')[1].split(/\n  \w+:\n/)[0];
  assert.match(job, /needs: \[plan, quality, package_linux_x86_64_cpu\]/);
  assert.match(job, /group: jy-rnr_gsxt2m73mgpcbbh/);
  assert.match(job, /labels: \[self-hosted, skippy-ephemeral, Linux, X64, rocm\]/);
  assert.match(job, /smoke: inference/);
  assert.doesNotMatch(job, /build-cli:|setup-host:/);
  assert.match(job, /SKIPPY_CI_ROCM_DEVICE_SMOKE: '1'/);
  assert.match(job, /TAR_OPTIONS: --no-same-owner/);
  assert.match(job, /--device=\/dev\/kfd --device=\/dev\/dri/);
  assert.match(job, /candidate-artifact: \$\{\{ inputs.candidate_artifact \}\}/);
  assert.match(job, /candidate-sha: \$\{\{ inputs.candidate_sha \}\}/);
  assert.ok(workflow.split('\n  gate:\n')[1].includes('package_linux_x86_64_rocm,'));
  assert.doesNotMatch(workflow, /^  (?:release_smoke|smoke)_linux_x86_64_rocm:/m);
  const condition = job.match(/^    if: (.+)$/m)[1];
  const selected = new Function('needs', `return (${condition})`);
  assert.equal(selected({ plan: { outputs: { profile: 'pr' } } }), false);
  assert.equal(selected({ plan: { outputs: { profile: 'full' } } }), true);
  const release = readFileSync(join(__dirname, '../workflows/release.yml'), 'utf8')
    .split('\n  validation:\n')[1].split('\n  documentation_validation:\n')[0];
  assert.match(release, /uses: .\/.github\/workflows\/ci.yml/);
  assert.match(release, /profile: full/);

  const composite = readFileSync(join(__dirname, '../actions/ci-package/action.yml'), 'utf8');
  const source = composite.indexOf('uses: ./.github/actions/source');
  const preflight = composite.indexOf('run: just ci-rocm-preflight');
  assert.ok(source >= 0 && source < preflight);
  assert.ok(preflight < composite.indexOf('run: bash scripts/ci-rocm-blas.sh'));
  assert.match(composite, /if: inputs.runtime-id == 'linux-x86_64-rocm' && inputs.smoke == 'inference'/);
});
