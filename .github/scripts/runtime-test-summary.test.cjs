const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { spawnSync } = require('node:child_process');

// Exercise the actual workflow shell step rather than a second validator.
const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
const section = workflow.split('\n  smoke:\n')[1].split('\n  summary:\n')[0] + '\n';
const script = section.match(/        run: \|\n((?:          .*\n)+)/)[1].replace(/^ {10}/gm, '');
const targets = JSON.parse(readFileSync(join(__dirname, '../../ci/targets.json'), 'utf8'));
const enabledHosts = targets.hosts.filter(h => h.enabled !== false &&
  (!targets.enabled_hosts || targets.enabled_hosts.includes(h.id))).map(h => h.id);
const matrix = { include: targets.runtimes.filter(r => r.enabled !== false && enabledHosts.includes(r.host)) };
const jobs = ['plan', ...matrix.include.map(r => `smoke_${r.id.replaceAll('-', '_')}`)];
const success = Object.fromEntries(jobs.map(name => [name, { result: 'success' }]));
const evaluate = (needs, runtimes = matrix) => spawnSync('bash', ['-euo', 'pipefail', '-c', script], {
  encoding: 'utf8', env: { ...process.env, SKIPPY_CI_NEEDS: JSON.stringify(needs), RUNTIME_MATRIX: JSON.stringify(runtimes) }
});

test('the full runtime roster matches the summary dependencies and passes', () => {
  const declared = section.match(/    needs: \[([^\]]+)\]/)[1].split(',').map(s => s.trim());
  assert.deepEqual(declared.sort(), [...jobs].sort());
  const result = evaluate(success);
  assert.equal(result.status, 0, result.stderr);
});

test('each missing, failed, skipped, cancelled or unfinished job fails the summary', () => {
  for (const job of jobs) {
    for (const result of ['failure', 'skipped', 'cancelled', 'queued', 'in_progress', null]) {
      const changed = structuredClone(success);
      changed[job].result = result;
      assert.notEqual(evaluate(changed).status, 0, `${job}: ${result}`);
    }
    const missing = structuredClone(success);
    delete missing[job];
    assert.notEqual(evaluate(missing).status, 0, job);
    missing.unexpected_job = { result: 'success' };
    assert.notEqual(evaluate(missing).status, 0, `${job}: replaced with unexpected job`);
  }
  assert.notEqual(evaluate({ ...success, extra_job: { result: 'success' } }).status, 0);
});

test('missing, empty, malformed and duplicate runtime selections cannot pass', () => {
  for (const invalid of [{}, { include: [] }, { include: null }, { include: [{ id: null }] },
    { include: [matrix.include[0], matrix.include[0]] }]) {
    assert.notEqual(evaluate(success, invalid).status, 0);
  }
});
