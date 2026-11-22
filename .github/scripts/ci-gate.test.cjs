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

function results({ code = true, inference = false, full = false, release = false, hostedRequiresNative = false } = {}) {
  const selected = code || inference;
  const required = ['changes'];
  if (selected) required.push('plan');
  if (code) required.push('quality');
  if (full) required.push('native_contracts', ...packageJobs);
  else if (inference) required.push(...prPackages);
  if (release) required.push(...releaseSmokes);
  const needs = Object.fromEntries(declared.map(job => [job, { result: required.includes(job) ? 'success' : 'skipped' }]));
  needs.changes.outputs = { code: String(code), inference: String(inference), hosted_requires_native: String(hostedRequiresNative) };
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
    assert.match(body, /^    needs: \[plan, quality[\],]/m);
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
  for (const job of ['changes', 'plan', 'quality', 'native_contracts', ...packageJobs]) {
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

test('hosted CI cannot satisfy a native inference requirement', () => {
  assert.throws(() => gate(results({ hostedRequiresNative: true })), /require native hardware qualification on an isolated runner/);
});

test('the repository switch moves every hosted job to Depot', () => {
  const workflows = ['ci', 'ci-images', 'ci-image-builds', 'docs', 'inference', 'llama-upgrade', 'llama-upgrade-checks', 'pr-checks', 'release'];
  for (const name of workflows) {
    const yaml = readFileSync(join(__dirname, '../workflows', name + '.yml'), 'utf8');
    const labels = [...yaml.matchAll(/^    runs-on: (.+)\n((?:      .+\n)*)/gm)]
      .map(([, label, continuation]) => `${label} ${continuation.trim().replaceAll(/\s+/g, ' ')}`.trim());
    assert.ok(labels.some(label => label.includes('SKIPPY_DEPOT_RUNNERS_ENABLED')), name);
    const smokeJobs = name === 'ci' ? workflow.split('\n  pr_image_builds:')[0] : '';
    const smokeLabels = [...smokeJobs.matchAll(/^    runs-on: (.+)$/gm)].map(([, label]) => label);
    assert.deepEqual(smokeLabels, name === 'ci' ? ['depot-ubuntu-24.04-4', 'depot-ubuntu-24.04-4'] : []);
    for (const label of labels.slice(smokeLabels.length)) {
      if (name.startsWith('llama-upgrade') && label === '${{ matrix.runner }}') continue;
      assert.match(label, /vars.SKIPPY_DEPOT_RUNNERS_ENABLED == 'true'.*depot-/, name);
    }
  }
});

test('ordinary PRs call protected workflows with scoped cache access and read-only permissions', () => {
  const workflows = { 'ci-pr': 'ci', 'ci-images-pr': 'ci', 'docs-pr': 'docs' };
  for (const [caller, callee] of Object.entries(workflows)) {
    const yaml = readFileSync(join(__dirname, '../workflows', caller + '.yml'), 'utf8');
    assert.ok(yaml.includes(`uses: Mesh-LLM/skippy/.github/workflows/${callee}.yml@main`));
    assert.match(yaml, caller === 'ci-pr' ? /^    cache-mode: read$/m : /^    cache-mode: none$/m);
    assert.doesNotMatch(yaml, /runs-on:|secrets:|: write$/m);
    const reusable = readFileSync(join(__dirname, '../workflows', callee + '.yml'), 'utf8');
    assert.doesNotMatch(reusable, /^(?:    )+\w+: write$/m);
  }
});

test('Depot compiler cache smoke runs only on manual protected main and caps its reader', () => {
  const [seed, read] = workflow.split('\n  sccache_seed:\n')[1]
    .split('\n  pr_image_builds:\n')[0].split('\n  sccache_read:\n');
  assert.match(seed, /github\.workflow_ref == 'Mesh-LLM\/skippy\/\.github\/workflows\/ci\.yml@refs\/heads\/main'/);
  assert.match(seed, /github\.event_name == 'workflow_dispatch' && inputs\.profile == 'sccache'/);
  assert.doesNotMatch(seed + read, /^    cache-mode:/m);
  assert.match(read, /^    needs: sccache_seed$/m);
  assert.match(read, /needs\.sccache_seed\.outputs\.scope/);
  assert.match(read, /read-only: 'true'/);
  const changes = workflow.split('\n  changes:\n')[1].split('\n  plan:\n')[0];
  assert.match(changes, /github\.event_name != 'workflow_dispatch' \|\| inputs\.profile != 'sccache'/);
});

test('the protected image PR bridge routes only the image workflow', () => {
  const bridge = workflow.split('\n  pr_image_builds:\n')[1].split('\n  changes:\n')[0];
  const admission = bridge.match(/if: >-\n([\s\S]+?)\n    uses:/)[1];
  const eligible = new Function('github', 'vars', 'startsWith', `return (${admission});`);
  const event = {
    repository: 'Mesh-LLM/skippy', event_name: 'pull_request',
    workflow_ref: 'Mesh-LLM/skippy/.github/workflows/ci-images-pr.yml@refs/pull/136/merge',
  };
  const vars = { SKIPPY_DEPOT_RUNNERS_ENABLED: 'true' };
  const startsWith = (value, prefix) => value.startsWith(prefix);
  assert.equal(eligible(event, vars, startsWith), true);
  assert.equal(eligible(event, {}, startsWith), false);
  assert.equal(eligible({ ...event, workflow_ref: event.workflow_ref.replace('ci-images-pr', 'ci-pr') }, vars, startsWith), false);
  assert.match(bridge, /uses: \.\/\.github\/workflows\/ci-image-builds\.yml/);
});

test('host CPU packages install Just on GitHub and Depot runners', () => {
  for (const row of rows) {
    const job = `package_${row.id.replaceAll('-', '_')}`;
    const body = workflow.split(`\n  ${job}:\n`)[1].split(/\n  \w+:\n/)[0];
    assert.equal(body.includes("setup-host: 'true'"), row.backend === 'cpu', job);
  }
  const action = readFileSync(join(__dirname, '../actions/ci-package/action.yml'), 'utf8');
  assert.match(action, /uses: \.\/\.github\/actions\/setup\n      if: inputs.setup-host == 'true'/);
});

test('manual compiler image smoke can skip publication on either runner provider', () => {
  const images = readFileSync(join(__dirname, '../workflows/ci-images.yml'), 'utf8');
  const repository = 'Mesh-LLM/skippy';
  const ref = 'refs/heads/main';
  for (const kind of ['cpu', 'gpu']) {
    const publish = images.split(`\n  ${kind}-publish:\n`)[1];
    const publishPolicy = publish.match(/if: >-\n([\s\S]+?)\n    runs-on:/)[1];
    const publishImage = new Function('github', 'inputs', `return (${publishPolicy});`);
    assert.equal(publishImage({ repository, ref, event_name: 'workflow_dispatch' }, { build_only: true }), false);
    assert.equal(publishImage({ repository, ref, event_name: 'workflow_dispatch' }, { build_only: false }), true);
    assert.equal(publishImage({ repository, ref, event_name: 'push' }, {}), true);
    assert.equal(publishImage({ repository, ref, event_name: 'pull_request' }, {}), false);
  }
});

test('native CI trusts only an immutable same-repository PR from a writer', async () => {
  const admission = workflow.split('      - name: Verify the same-repository PR author\n')[1].split('\n  plan:\n')[0];
  const script = admission.split('          script: |\n')[1];
  const evaluate = new Function('github', 'context', 'return (async () => {\n' + script + '\n})()');
  const candidate = { head: { sha: 'a'.repeat(40), repo: { id: 42 } }, base: { ref: 'main' }, user: { login: 'maintainer' } };
  const assertAdmission = (pr, permission) => evaluate({ rest: { repos: {
    getCollaboratorPermissionLevel: async ({ username }) => {
      assert.equal(username, 'maintainer');
      return { data: { permission } };
    },
  } } }, { repo: {}, payload: { pull_request: pr, repository: { id: 42 } } });
  await assert.doesNotReject(() => assertAdmission(candidate, 'write'));
  await assert.rejects(() => assertAdmission(candidate, 'read'), /trusted PR author/);
  await assert.rejects(() => assertAdmission({ ...candidate, head: { ...candidate.head, repo: { id: 43 } } }, 'admin'), /same-repository PR/);
  await assert.rejects(() => assertAdmission({ ...candidate, head: { ...candidate.head, sha: 'main' } }, 'admin'), /immutable source/);
});

test('main can reuse only a receipt for the exact release dispatch and candidate', async () => {
  const changes = workflow.split('\n  changes:\n')[1].split('\n  plan:\n')[0];
  const receiptScript = changes.split('          script: |\n')[1].split('\n      - uses:')[0];
  const evaluateReceipt = new Function('github', 'context', 'core',
    'return (async () => {\n' + receiptScript + '\n})()');
  const sha = 'a'.repeat(40);
  const base = 'b'.repeat(40);
  const full_name = 'Mesh-LLM/skippy';
  const commit = {
    commit: { message: 'release: prepare v0.81.0\n\nSkippy-Release-Run: 123' },
    parents: [{ sha: base }],
  };
  const run = {
    path: '.github/workflows/release.yml',
    event: 'workflow_dispatch',
    head_branch: 'main',
    head_sha: base,
    head_repository: { full_name },
  };
  const receipt = { name: 'validated-release-' + sha, expired: false };
  const context = { sha, repo: {}, payload: { before: base, repository: { full_name } } };
  async function checked(candidate, workflowRun, artifact) {
    const out = {};
    const github = { rest: {
      repos: { getCommit: async () => ({ data: candidate }) },
      actions: {
        getWorkflowRun: async ({ run_id }) => {
          assert.equal(run_id, '123');
          return { data: workflowRun };
        },
        listWorkflowRunArtifacts: async ({ name }) => {
          assert.equal(name, receipt.name);
          return { data: { artifacts: [artifact] } };
        },
      },
    } };
    const core = {
      info() {},
      warning(message) { assert.fail(message); },
      setOutput(key, value) { out[key] = value; },
    };
    await evaluateReceipt(github, context, core);
    return out.verified;
  }
  assert.equal(await checked(commit, run, receipt), 'true');
  assert.equal(await checked({ ...commit, parents: [{ sha }] }, run, receipt), undefined);
  assert.equal(await checked(commit, { ...run, head_branch: 'other' }, receipt), undefined);
  assert.equal(await checked(commit, run, { ...receipt, name: 'other-receipt' }), undefined);
  assert.equal(await checked(commit, run, { ...receipt, expired: true }), undefined);
});
