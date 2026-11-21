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

test('Depot placement requires opt-in from protected CI or release validation', () => {
  const expression = workflow.match(/^      linux-runner-provider: >-\n\s+\$\{\{([\s\S]+?)\}\}/m)[1];
  const evaluateProvider = new Function('github', 'vars', 'inputs', `return (${expression});`);
  const main = 'Mesh-LLM/skippy/.github/workflows/ci.yml@refs/heads/main';
  const release = main.replace('ci.yml', 'release.yml');
  function provider(event_name, { workflow_ref = main, enabled = '', use_depot = false, artifact_scope = '' } = {}) {
    return evaluateProvider({ workflow_ref, event_name }, { SKIPPY_DEPOT_RUNNERS_ENABLED: enabled }, { use_depot, artifact_scope });
  }

  for (const event of ['push', 'schedule', 'workflow_dispatch']) {
    assert.equal(provider(event), 'github', event);
    assert.equal(provider(event, { enabled: 'true' }), 'depot', event);
  }
  assert.equal(provider('workflow_dispatch', { enabled: 'false', use_depot: true }), 'depot');
  for (const event of ['pull_request', 'pull_request_target', 'workflow_call']) {
    assert.equal(provider(event, { enabled: 'true', use_depot: true }), 'github', event);
  }
  for (const workflow_ref of [
    main.replace('heads/main', 'heads/topic'),
    main.replace('heads/main', 'tags/v1.0.0'),
    main.replace('ci.yml', 'llama-upgrade.yml'),
    main.replace('Mesh-LLM', 'fork-owner'),
  ]) {
    assert.equal(provider('workflow_dispatch', { workflow_ref, enabled: 'true', use_depot: true }), 'github', workflow_ref);
  }

  const releaseOptions = { workflow_ref: release, enabled: 'true', artifact_scope: 'release' };
  for (const event of ['schedule', 'workflow_dispatch']) {
    assert.equal(provider(event, releaseOptions), 'depot', event);
    assert.equal(provider(event, { ...releaseOptions, enabled: '', use_depot: true }), 'github', event);
    assert.equal(provider(event, { ...releaseOptions, artifact_scope: 'ci' }), 'github', event);
  }
  assert.equal(provider('pull_request_target', releaseOptions), 'github');
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

test('Depot compiler image canaries build on protected main without publishing', () => {
  const images = readFileSync(join(__dirname, '../workflows/ci-images.yml'), 'utf8');
  const repository = 'Mesh-LLM/skippy';
  const ref = 'refs/heads/main';
  const main = `${repository}/.github/workflows/ci-images.yml@${ref}`;
  for (const [kind, runners] of [['cpu', ['ubuntu-24.04']], ['gpu', ['ubuntu-24.04', 'ubuntu-24.04-arm']]]) {
    const build = images.split(`\n  ${kind}-build:\n`)[1].split(`\n  ${kind}-publish:\n`)[0];
    const publish = images.split(`\n  ${kind}-publish:\n`)[1];
    const buildPolicy = build.match(/runs-on: >-\n\s+\$\{\{([\s\S]+?)\}\}/)[1];
    const publishPolicy = publish.match(/if: >-\n([\s\S]+?)\n    runs-on:/)[1];
    const placement = new Function('github', 'vars', 'inputs', 'matrix', 'format', `return (${buildPolicy});`);
    const publishImage = new Function('github', 'inputs', `return (${publishPolicy});`);

    for (const runner of runners) {
      function choose(event_name, { enabled = '', use_depot = false, workflow_ref = main } = {}) {
        return placement({ event_name, workflow_ref }, { SKIPPY_DEPOT_IMAGE_RUNNERS_ENABLED: enabled },
          { use_depot }, { runner }, (pattern, label) => pattern.replace('{0}', label));
      }
      assert.equal(choose('push'), runner);
      assert.equal(choose('push', { enabled: 'true' }), `depot-${runner}-4`);
      assert.equal(choose('workflow_dispatch', { use_depot: true }), `depot-${runner}-4`);
      assert.equal(choose('workflow_dispatch', { workflow_ref: main.replace('heads/main', 'heads/topic'), use_depot: true }), runner);
      assert.equal(choose('pull_request', { enabled: 'true', use_depot: true }), runner);
    }
    assert.equal(publishImage({ repository, ref, event_name: 'workflow_dispatch' }, { use_depot: true }), false);
    assert.equal(publishImage({ repository, ref, event_name: 'workflow_dispatch' }, { use_depot: false }), true);
    assert.equal(publishImage({ repository, ref, event_name: 'push' }, {}), true);
    assert.equal(publishImage({ repository, ref, event_name: 'pull_request' }, {}), false);
    assert.doesNotMatch(publish.split(/\n  [\w-]+:\n/)[0], /runs-on: .*depot-/);
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
