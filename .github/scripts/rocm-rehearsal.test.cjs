const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');

const source = readFileSync(join(__dirname, '../actions/rocm-rehearsal-workspace/ownership.cjs'), 'utf8');

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
  const action = readFileSync(join(__dirname, '../actions/rocm-rehearsal-workspace/action.yml'), 'utf8');
  assert.match(action, /post: ownership.cjs\n  post-if: always\(\)/);
  const workflow = readFileSync(join(__dirname, '../workflows/rocm-rehearsal.yml'), 'utf8');
  assert.ok(workflow.indexOf('uses: ./.github/actions/rocm-rehearsal-workspace') <
            workflow.indexOf('uses: ./.github/actions/ci-package'));
  assert.doesNotMatch(workflow, /^  pull_request(?:_target)?:/m);
});
