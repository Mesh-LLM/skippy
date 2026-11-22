const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const workflow = name => readFileSync(join(__dirname, '../workflows', name), 'utf8');
const jobs = text => text.split(/^jobs:\n/m)[1].split(/(?=^  [\w-]+:\n)/m).filter(Boolean);
const permissions = text => [...text.matchAll(/^    permissions:\n((?:      [\w-]+: \w+\n)+)/gm)]
  .flatMap(match => [...match[1].matchAll(/^      ([\w-]+): (\w+)$/gm)]
    .map(([, name, access]) => [name, access]));

test('the single reusable upgrade CI call grant the read permissions requested by reusable CI', () => {
  // GitHub rejects the entire workflow before any job starts if a callee
  // requests a permission omitted from its caller's explicit permission map.
  const required = permissions(workflow('ci.yml'));
  assert.ok(required.length > 0);
  const callers = jobs(workflow('llama-upgrade-checks.yml'))
    .filter(job => job.includes('uses: ./.github/workflows/ci.yml'));
  assert.equal(callers.length, 1);
  for (const job of callers) {
    const granted = Object.fromEntries(permissions(job));
    for (const [name, access] of required) {
      assert.equal(access, 'read', `unexpected write permission in qualification: ${name}`);
      assert.equal(granted[name], access, `${job.split('\n')[0].trim()} lacks ${name}: ${access}`);
    }
    assert.ok(Object.values(granted).every(access => access === 'read'));
  }
});


test('normal upgrades run once and repaired candidates get fresh qualification', () => {
  const controller = workflow('llama-upgrade.yml');
  assert.doesNotMatch(controller, /independent_ci:|independent_families:/);
  assert.match(controller, /needs\.qualify\.result == 'failure'/);
  assert.match(controller, /candidate_artifact: llama-repaired-candidate/);
  assert.match(controller, /--repair-bundle target\/llama-repair\/candidate\/candidate.bundle/);
  assert.match(controller, /needs\.requalify\.result == 'success'/);
  const checks = workflow('llama-upgrade-checks.yml');
  assert.doesNotMatch(checks, /backend: metal/);
  assert.match(checks, /backend: cpu/);
  assert.match(checks, /backend: cuda/);
  assert.match(checks, /max-parallel: 3/);
  const dgx = checks.split('  dgx:')[1].split('  gate:')[0];
  assert.equal([...dgx.matchAll(/^          - qwen/gm)].length, 7);
});
