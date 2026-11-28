const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const admit = require('./fork-admission.cjs');
const { routes } = require('./pr-trigger-paths.cjs');
const forkRuns = require('./pr-fork-runs.cjs');
const sha = 'a'.repeat(40);
const run = { id: 123, event: 'pull_request', path: '.github/workflows/ci.yml',
  repository: { id: 42 }, head_repository: { id: 43, owner: { login: 'outside' } }, head_sha: sha, head_branch: 'feature',
  status: 'completed', conclusion: 'success', run_attempt: 1 };
const pr = { number: 12, state: 'open', changed_files: 1,
  head: { sha, ref: 'feature', repo: { id: 43 } }, base: { repo: { id: 42 } } };

async function admission(options = {}) {
  const outputs = {};
  const original = { ...run, ...options.run };
  const rest = { actions: { getWorkflowRun: async () => ({ data: original }) },
    pulls: { list: 'pulls', get: async () => ({ data: options.current || pr }), listFiles: 'files' } };
  await admit({ github: { rest, paginate: async (method, parameters) => {
    if (method === 'pulls') {
      assert.equal(parameters.head, 'outside:feature');
      return options.pulls || [pr];
    }
    return options.files || [{ filename: 'crates/skippy-cli/src/main.rs' }];
  } }, context: { eventName: options.event || 'workflow_run',
    repo: { owner: 'Mesh-LLM', repo: 'skippy' },
    payload: { repository: { id: 42 }, workflow_run: { ...original, ...options.trigger } } },
    core: { setOutput: (name, value) => { outputs[name] = value; } }, depot: options.depot || false });
  return outputs;
}

test('successful approved fork producer admits its exact current source and protected path decisions', async () => {
  const outputs = await admission();
  assert.equal(outputs.source, sha);
  assert.equal(outputs.number, '12');
  assert.equal(outputs.ci, 'true');
  assert.equal(outputs.inference, 'true');
  assert.deepEqual(JSON.parse(outputs.routes), { automation: true, code: true, packages: true, inference: true });
  await admission({ depot: true });
});

test('failed, unapproved, stale-attempt, other-producer, same-repository and foreign runs cannot admit', async () => {
  for (const change of [{ conclusion: 'action_required' }, { conclusion: 'failure' },
    { status: 'in_progress' }, { event: 'push' }, { event: 'pull_request_target' },
    { path: '.github/workflows/spoof.yml' }, { repository: { id: 99 } },
    { head_repository: { id: 42 } }, { head_repository: null }, { head_sha: 'main' }]) {
    await assert.rejects(admission({ run: change }), /Not a successful current fork CI/);
  }
  await assert.rejects(admission({ trigger: { run_attempt: 2 } }), /Not a successful/);
  await assert.rejects(admission({ trigger: { head_sha: 'b'.repeat(40) } }), /Not a successful/);
  await assert.rejects(admission({ run: { path: '.github/workflows/ci-pr.yml' } }), /Not a successful/);
  await assert.rejects(admission({ event: 'pull_request' }), /Not a successful/);
});

test('empty fork payload PR lists are resolved by API, but stale, closed, foreign PRs and truncated files fail', async () => {
  await admission({ trigger: { pull_requests: [] } });
  for (const candidate of [{ ...pr, state: 'closed' },
    { ...pr, head: { ...pr.head, sha: 'b'.repeat(40) } },
    { ...pr, head: { ...pr.head, ref: 'other' } },
    { ...pr, head: { ...pr.head, repo: { id: 44 } } },
    { ...pr, base: { repo: { id: 99 } } }]) {
    await assert.rejects(admission({ pulls: [candidate] }), /not the current head/);
  }
  await assert.rejects(admission({ current: { ...pr, head: { ...pr.head, sha: 'b'.repeat(40) } } }), /changed/);
  await assert.rejects(admission({ current: { ...pr, changed_files: 2 } }), /Incomplete/);
});

test('protected routes preserve exclusions and renamed paths without rebuilding packages for qualification-only work', () => {
  assert.deepEqual(routes([{ filename: 'scripts/inference-plan.py' }]),
    { automation: true, code: false, packages: false, inference: true });
  assert.deepEqual(routes([{ filename: 'scripts/tests/inference_contracts.py' }]),
    { automation: true, code: false, packages: false, inference: false });
  assert.deepEqual(routes([{ filename: 'README.md' }]),
    { automation: false, code: false, packages: false, inference: false });
  assert.equal(routes([{ filename: 'README.md', previous_filename: 'crates/skippy-api/src/lib.rs' }]).packages, true);
});

test('only the protected workflow_run producer title binds main-based runs to fork commits', () => {
  const native = { event: 'workflow_run', path: '.github/workflows/fork-native.yml', display_title: sha };
  assert.equal(forkRuns.source(native), sha);
  for (const change of [{ event: 'pull_request' }, { path: '.github/workflows/spoof.yml' },
    { display_title: 'Ignored fork CI trigger' }, { display_title: 'Approved fork CI: main' }]) {
    assert.equal(forkRuns.source({ ...native, ...change }), null);
  }
});

test('wrapper uses protected reusable pipelines, receives no fork artifacts and keeps dispatch secrets out of CI', () => {
  const workflow = readFileSync(join(__dirname, '../workflows/fork-native.yml'), 'utf8');
  assert.match(workflow, /workflow_run:\n +workflows: \[CI\]/);
  assert.match(workflow, /ref: main/);
  assert.match(workflow, /runs-on: ubuntu-24\.04/);
  assert.doesNotMatch(workflow, /SKIPPY_DEPOT_RUNNERS_ENABLED|depot-ubuntu/);
  assert.doesNotMatch(workflow, /download-artifact|secrets: inherit|environment:/);
  assert.match(workflow, /uses: \.\/\.github\/workflows\/ci.yml/);
  assert.match(workflow, /uses: \.\/\.github\/workflows\/inference.yml/);
  const ci = workflow.split('\n  ci:\n')[1].split('\n  inference:\n')[0];
  assert.doesNotMatch(ci, /secrets|: write/);
  // Branch PRs build CUDA in native mode; a different mode is a bigger build in another compiler-cache namespace.
  assert.match(ci, /\n      cuda_architecture_mode: native\n/);
});

test('the reusable CI rejects other callers, stale heads and malformed protected routes', async () => {
  const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
  const script = workflow.split('      - name: Verify the approved-fork caller')[1]
    .split('          script: |\n')[1].split('      - name: Reject custom')[0];
  const evaluate = new (Object.getPrototypeOf(async function () {}).constructor)('process', 'context', 'github', 'core', script);
  const env = { WORKFLOW_REF: 'Mesh-LLM/skippy/.github/workflows/fork-native.yml@refs/heads/main',
    SOURCE: sha, PR: '12', ROUTES: JSON.stringify({ automation: true, code: true, packages: true }) };
  const invoke = (overrides = {}, current = pr) => evaluate({ env: { ...env, ...overrides } },
    { repo: {}, payload: { workflow_run: { head_sha: sha } } },
    { rest: { pulls: { get: async () => ({ data: current }) } } }, { setOutput() {} });
  await invoke();
  for (const overrides of [{ WORKFLOW_REF: env.WORKFLOW_REF.replace('main', 'feature') },
    { SOURCE: 'b'.repeat(40) }, { PR: '12;evil' }, { ROUTES: '{}' }]) await assert.rejects(invoke(overrides));
  await assert.rejects(invoke({}, { ...pr, state: 'closed' }), /head changed/);
});

test('fork inference is source-bound on failure and does not share one concurrency slot across PRs', () => {
  const workflow = readFileSync(join(__dirname, '../workflows/inference.yml'), 'utf8');
  assert.match(workflow, /github.event.pull_request.number \|\| inputs.approved_fork_pr \|\| github.ref/);
  assert.equal((workflow.match(/github.event.workflow_run.head_sha \|\| github.sha/g) || []).length, 2);
  const ci = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
  assert.doesNotMatch(ci, /^(?:    )+\w[\w-]*: write$/m);
});

test('protected trigger and title admit ordinary fork CI even with base Depot enabled', () => {
  const workflow = readFileSync(join(__dirname, '../workflows/fork-native.yml'), 'utf8');
  const condition = workflow.split('    if: >-\n')[1].split('\n    runs-on:')[0]
    .split('\n').filter(line => !line.trim().startsWith('#')).join('\n').trim();
  const title = workflow.split('run-name: >-\n  ${{ ')[1].split(' }}')[0];
  const github = { repository: 'Mesh-LLM/skippy', event: { repository: { id: 42 }, workflow_run: run } };
  for (const enabled of ['true', '', 'false']) {
    const vars = { SKIPPY_DEPOT_RUNNERS_ENABLED: enabled };
    const check = new Function('github', 'vars', `return (${condition})`);
    const name = new Function('github', 'vars', `return (${title})`);
    assert.equal(check(github, vars), true);
    assert.equal(name(github, vars), sha);
    for (const patch of [{ path: '.github/workflows/ci-pr.yml' }, { conclusion: 'action_required' },
      { event: 'push' }, { head_repository: { id: 42 } }]) {
      const other = { ...github, event: { ...github.event, workflow_run: { ...run, ...patch } } };
      assert.equal(check(other, vars), false);
      assert.equal(name(other, vars), 'Ignored fork CI trigger');
    }
  }
});


test('approved fork Cargo cache keys and restores stay outside main and branch namespaces', () => {
  const cache = readFileSync(join(__dirname, '../actions/cache/action.yml'), 'utf8');
  const keys = cache.split('\n').filter(line => /cargo-(downloads|build)-v1-/.test(line));
  assert.equal(keys.length, 4); // save and restore for downloads and build snapshots
  for (const line of keys) {
    const expression = line.match(/\$\{\{ (github.event_name.*?) \}\}/)[1];
    const prefix = new Function('github', `return (${expression})`);
    for (const [event_name, expected] of [
      ['workflow_run', 'approved-fork-pr-'], ['pull_request_target', 'same-repo-pr-'],
      ['pull_request', ''], ['push', ''], ['schedule', ''], ['workflow_dispatch', ''],
    ]) assert.equal(prefix({ event_name }), expected, `${event_name}: ${line}`);
  }
});


test('test support and display reports admit offline fork CI without native work', async () => {
  for (const filename of ['crates/skippy-runtime/src/test_support/libraries.rs',
    'scripts/lib/inference_labels.py', 'scripts/lib/inference_health.py']) {
    const outputs = await admission({ files: [{ filename }] });
    assert.equal(outputs.ci, 'true');
    assert.equal(outputs.inference, 'false');
    const selected = JSON.parse(outputs.routes);
    assert.equal(selected.packages, false);
    assert.equal(selected.inference, false);
    assert.equal(selected.code, filename.startsWith('crates/'));
  }
  const outputs = await admission({ files: [{ filename: 'ci/inference.just' }] });
  assert.equal(outputs.inference, 'true');
  assert.equal(JSON.parse(outputs.routes).packages, false);
});

test('fork aggregate does not wait for excluded inference jobs but still requires offline CI', async () => {
  const checked = { ...run, event: 'workflow_run' };
  const successful = ['admission', 'ci / CI'].map((name, index) =>
    ({ name, id: index + 1, status: 'completed', conclusion: 'success', run_attempt: 1 }));
  const files = [{ filename: 'crates/skippy-runtime/src/test_support/libraries.rs' }];
  const github = { rest: { actions: { listJobsForWorkflowRun: 'jobs' } },
    paginate: async () => successful };
  await forkRuns.requireForkJobs(github, { owner: 'Mesh-LLM', repo: 'skippy' }, checked, files);
  await assert.rejects(forkRuns.requireForkJobs({ ...github, paginate: async () => successful.slice(0, 1) },
    { owner: 'Mesh-LLM', repo: 'skippy' }, checked, files), /ci \/ CI: missing or incomplete/);
  await assert.rejects(forkRuns.requireForkJobs(github, { owner: 'Mesh-LLM', repo: 'skippy' }, checked,
    [{ filename: 'ci/inference.just' }]), /Inference qualification: plan: missing or incomplete/);
});
