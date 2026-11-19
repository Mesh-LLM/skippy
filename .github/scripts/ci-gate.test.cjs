const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
const section = workflow.split('\n  gate:\n')[1];
const script = section.split('          script: |\n')[1].replace(/^ {12}/gm, '');
const evaluate = new Function('process', script);
const declared = section.match(/    needs: \[([\s\S]+?)\]/)[1].split(',').map(s => s.trim());
const targets = JSON.parse(readFileSync(join(__dirname, '../../ci/targets.json'), 'utf8'));
const rows = targets.runtimes.filter(row => row.enabled !== false && targets.enabled_hosts.includes(row.host));
const packageJobs = rows.map(row => `package_${row.id.replaceAll('-', '_')}`);
const prPackages = ['package_darwin_aarch64_metal', 'package_linux_x86_64_cuda13'];
const releaseSmokes = ['release_smoke_linux_x86_64_cuda12', 'release_smoke_linux_aarch64_cuda12', 'release_smoke_linux_aarch64_cuda13'];

function results({ code = true, inference = false, full = false, release = false } = {}) {
  const selected = code || inference;
  const required = ['changes'];
  if (selected) required.push('plan');
  if (code) required.push('automation', 'quality');
  if (full) required.push('native_contracts', ...packageJobs);
  else if (inference) required.push(...prPackages);
  if (release) required.push(...releaseSmokes);
  const needs = Object.fromEntries(declared.map(job => [job, { result: required.includes(job) ? 'success' : 'skipped' }]));
  needs.changes.outputs = { code: String(code), inference: String(inference) };
  if (selected) needs.plan.outputs = { profile: full ? 'full' : 'pr', runtimes: JSON.stringify({ include: rows }) };
  return needs;
}

function gate(needs, release = false) {
  evaluate({ env: { CHECK_RESULTS: JSON.stringify(needs), RELEASE_SMOKE: String(release) } });
}

test('all eight runtime packages match the planned matrix and depend on the canary', () => {
  assert.equal(packageJobs.length, 8);
  assert.deepEqual(declared.filter(job => job.startsWith('package_')).sort(), packageJobs.sort());
  for (const row of rows) {
    const job = `package_${row.id.replaceAll('-', '_')}`;
    const body = workflow.split(`\n  ${job}:\n`)[1].split(/\n  \w+:\n/)[0];
    assert.match(body, /^    needs: \[plan, quality, automation[\],]/m);
    assert.ok(body.includes(`runtime-id: ${row.id}\n`));
    if (row.backend !== 'cpu') assert.ok(body.includes(`package_${row.host.replaceAll('-', '_')}_cpu`));
    assert.equal(body.includes("needs.changes.outputs.inference == 'true'"), prPackages.includes(job));
  }
  assert.doesNotThrow(() => gate(results({ full: true })));
});

test('only inference pull requests require the two package and smoke jobs', () => {
  for (const options of [{}, { code: false }, { inference: true }, { code: false, inference: true }]) {
    assert.doesNotThrow(() => gate(results(options)));
  }
  const needs = results({ inference: true });
  for (const job of prPackages) {
    const missingSmoke = structuredClone(needs);
    missingSmoke[job].result = 'skipped';
    assert.throws(() => gate(missingSmoke), new RegExp(job));
  }
  const unexpectedSmoke = results();
  unexpectedSmoke[prPackages[0]].result = 'success';
  assert.throws(() => gate(unexpectedSmoke), /must be skipped/);
});

test('missing, skipped, failed and cancelled required work cannot pass full CI', () => {
  for (const job of ['changes', 'plan', 'automation', 'quality', 'native_contracts', ...packageJobs]) {
    for (const result of ['skipped', 'failure', 'cancelled', undefined]) {
      const needs = results({ full: true });
      if (result === undefined) delete needs[job];
      else needs[job].result = result;
      assert.throws(() => gate(needs), undefined, `${job}: ${result}`);
    }
  }
  const invalid = results({ full: true });
  invalid.plan.outputs.runtimes = JSON.stringify({ include: rows.map(() => rows[0]) });
  assert.throws(() => gate(invalid), /active runtime matrix/);
});

test('release validation requires the three other GPU smokes', () => {
  assert.doesNotThrow(() => gate(results({ full: true, release: true }), true));
  assert.throws(() => gate(results({ full: true }), true), /release_smoke_/);
  assert.throws(() => gate(results({ full: true, release: true })), /release_smoke_/);
  assert.throws(() => gate(results(), true), /requires full CI/);
});
