const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const aggregate = require('./pr-checks.cjs');
const sha = '1'.repeat(40);
const run = (path, id = 1, extra = {}) => ({
  id, workflow_id: path, path: `.github/workflows/${path}`, name: path,
  event: 'pull_request', head_sha: sha, pull_requests: [{ number: 52 }],
  status: 'completed', conclusion: 'success', ...extra
});

async function evaluate(options = {}) {
  const calls = [], failures = [];
  const pr = { number: 52, state: 'open', head: { sha } };
  let reads = 0;
  const rest = {
    pulls: { get: async () => ({ data: ++reads > 1 && options.current ? options.current : { ...pr, ...options.pr } }), listFiles: 'files' },
    actions: { listWorkflowRunsForRepo: 'runs' },
    checks: { listForRef: 'checks' },
    repos: { listPullRequestsAssociatedWithCommit: 'prs', listCommitStatusesForRef: 'statuses',
      createCommitStatus: async value => calls.push(value) }
  };
  const data = { files: options.files || [], runs: options.runs || [run('ci.yml'), run('inference.yml')],
    checks: options.checks || [], statuses: options.statuses || [], prs: [pr] };
  const github = { rest, paginate: async method => {
    if (options.apiError === method) throw new Error('GitHub API unavailable');
    return structuredClone(data[method]);
  } };
  const context = { repo: { owner: 'general-inference', repo: 'skippy' }, runId: 42,
    payload: options.payload || { pull_request: pr } };
  await aggregate({ github, context, core: { info() {}, setFailed: message => failures.push(message) } });
  assert.ok(calls.every(c => c.sha === sha && c.context === 'PR checks'));
  return { state: calls.at(-1)?.state, calls, failures };
}

test('success for all completed work and explicit skips; no model claim', async () => {
  for (const conclusion of ['success', 'skipped', 'neutral']) {
    const result = await evaluate({ runs: [run('ci.yml'), run('inference.yml', 2, { conclusion })] });
    assert.equal(result.state, 'success');
    assert.deepEqual(result.failures, []);
  }
});

test('missing, queued, running and waiting workflows remain pending', async () => {
  assert.equal((await evaluate({ runs: [] })).state, 'pending');
  for (const status of ['queued', 'in_progress', 'waiting', 'pending']) {
    assert.equal((await evaluate({ runs: [run('ci.yml', 1, { status }), run('inference.yml')] })).state, 'pending');
  }
});

test('every failed or cancelled workflow blocks, including additional workflows', async () => {
  for (const conclusion of ['failure', 'cancelled', 'timed_out', 'startup_failure', 'action_required', null]) {
    const result = await evaluate({ runs: [run('ci.yml'), run('inference.yml'), run('extra.yml', 3, { conclusion })] });
    assert.equal(result.state, 'failure');
    assert.equal(result.failures.length, 1);
  }
});

test('combined image workflow cannot be missed before it is scheduled', async () => {
  const workflow = readFileSync(join(__dirname, '../workflows/ci-images.yml'), 'utf8');
  const triggers = [...workflow.matchAll(/paths: \[([^\]]+)\]/g)];
  const expectedPaths = ['.github/workflows/ci-images.yml', 'ci/Dockerfile*', 'ci/targets.json', 'rust-toolchain.toml', 'Justfile'];
  assert.equal(triggers.length, 2);
  for (const trigger of triggers) assert.deepEqual(JSON.parse('[' + trigger[1].replaceAll("'", '"') + ']'), expectedPaths);
  for (const filename of [
    'ci/Dockerfile', 'ci/Dockerfile.cuda', 'ci/Dockerfile.gpu', 'ci/Dockerfile.gpu.dockerignore',
    'ci/targets.json', 'rust-toolchain.toml', 'Justfile', '.github/workflows/ci-images.yml'
  ]) {
    assert.equal((await evaluate({ files: [{ filename }] })).state, 'pending');
    assert.equal((await evaluate({ files: [{ filename }], runs: [run('ci.yml'), run('inference.yml', 2), run('ci-images.yml', 3)] })).state, 'success');
  }
  for (const filename of ['.gi/agents/code-review.yaml', 'docs-site/index.mdx']) {
    assert.equal((await evaluate({ files: [{ filename }] })).state, 'success');
  }
  assert.equal((await evaluate({ files: [{ filename: 'archive/old-container', previous_filename: 'ci/Dockerfile' }] })).state, 'pending');
});

test('latest run and attempt govern reruns; old cancellations do not poison new results', async () => {
  const runs = [run('ci.yml'), run('inference.yml', 2, { conclusion: 'cancelled' }), run('inference.yml', 3)];
  assert.equal((await evaluate({ runs })).state, 'success');
  runs.push(run('inference.yml', 4, { status: 'in_progress', conclusion: null, run_attempt: 2 }));
  assert.equal((await evaluate({ runs })).state, 'pending');
  runs[3].status = 'completed'; runs[3].conclusion = 'failure';
  assert.equal((await evaluate({ runs })).state, 'failure');
});

test('other sources, other PRs, main pushes and the controller do not satisfy missing work', async () => {
  for (const extra of [{ head_sha: '2'.repeat(40) }, { pull_requests: [{ number: 53 }] }, { event: 'push' }]) {
    assert.equal((await evaluate({ runs: [run('ci.yml'), run('inference.yml', 2, extra)] })).state, 'pending');
  }
  assert.equal((await evaluate({ runs: [run('ci.yml'), run('inference.yml'), run('pr-checks.yml', 9, { status: 'in_progress' })] })).state, 'success');
});

test('independent check runs and latest commit statuses also block or wait', async () => {
  for (const state of ['failure', 'pending', 'success']) {
    assert.equal((await evaluate({ statuses: [{ context: 'external', state }, { context: 'external', state: 'failure' }, { context: 'PR checks', state: 'pending' }] })).state, state);
  }
  for (const conclusion of ['failure', 'cancelled', 'timed_out']) {
    assert.equal((await evaluate({ checks: [{ name: 'External test', status: 'completed', conclusion }] })).state, 'failure');
  }
  assert.equal((await evaluate({ checks: [{ name: 'External test', status: 'queued' }] })).state, 'pending');
  assert.equal((await evaluate({ checks: [{ name: 'Evaluate aggregate PR checks', app: { slug: 'github-actions' }, status: 'in_progress' }] })).state, 'success');
  assert.equal((await evaluate({ checks: [
    { id: 1, name: 'Test', status: 'completed', conclusion: 'cancelled' },
    { id: 2, name: 'Test', status: 'completed', conclusion: 'success' }
  ] })).state, 'success');
});

test('API failures cannot become successful aggregate results', async () => {
  for (const apiError of ['files', 'runs', 'checks', 'statuses']) {
    const result = await evaluate({ apiError });
    assert.equal(result.state, 'failure');
    assert.equal(result.failures.length, 1);
  }
});

test('closed and stale PR events cannot write a successful result', async () => {
  assert.equal((await evaluate({ pr: { state: 'closed' } })).calls.length, 0);
  assert.equal((await evaluate({ pr: { head: { sha: '2'.repeat(40) } } })).calls.length, 0);
  assert.equal((await evaluate({ current: { state: 'open', head: { sha: '2'.repeat(40) } } })).state, 'pending');
});

test('workflow completion, external-check and status events re-evaluate the exact PR', async () => {
  for (const payload of [
    { workflow_run: { head_sha: sha, pull_requests: [{ number: 52 }] } },
    { workflow_run: { head_sha: sha, pull_requests: [] } },
    { check_run: { head_sha: sha, pull_requests: [{ number: 52 }] } },
    { sha }
  ]) assert.equal((await evaluate({ payload })).state, 'success');
});
