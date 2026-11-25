const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const aggregate = require('./pr-checks.cjs');
const expectedWorkflows = require('./pr-trigger-paths.cjs');
const sha = '1'.repeat(40);
const body = readFileSync(join(__dirname, '../PULL_REQUEST_TEMPLATE.md'), 'utf8').replaceAll('- [ ]', '- [x]');
const inferenceChange = { filename: 'crates/skippy-cli/src/main.rs' };
const inferenceJobs = () => ['plan', 'summary', ...JSON.parse(readFileSync(
  join(__dirname, '../../ci/inference-ladder.json'), 'utf8')).models.filter(model => model.pr).flatMap(model =>
  [`qualification (${model.id}-standalone)`, ...(model.split ? [`qualification (${model.id}-split)`] : [])])]
  .map((name, index) => ({ id: index + 1, name, run_attempt: 1, status: 'completed', conclusion: 'success' }));
const run = (path, id = 1, extra = {}) => ({
  id, workflow_id: path, path: `.github/workflows/${path}`, name: path,
  event: ['ci.yml', 'inference.yml'].includes(path) ? 'pull_request_target' : 'pull_request',
  head_sha: sha, pull_requests: [{ number: 52 }],
  status: 'completed', conclusion: 'success', run_attempt: 1, ...extra
});

async function evaluate(options = {}) {
  const calls = [], failures = [], jobRequests = [];
  const pr = { number: 52, state: 'open', head: { sha, repo: { id: 42 } }, base: { repo: { id: 42 } }, body };
  let reads = 0;
  const rest = {
    pulls: { list: 'prs', get: async () => ({ data: ++reads > 1 && options.current ? options.current : { ...pr, ...options.pr } }), listFiles: 'files' },
    actions: { listWorkflowRunsForRepo: 'runs', listWorkflowRuns: 'forkruns', listJobsForWorkflowRun: 'jobs' },
    checks: { listForRef: 'checks' },
    repos: { listPullRequestsAssociatedWithCommit: 'prs', listCommitStatusesForRef: 'statuses',
      createCommitStatus: async value => calls.push(value) }
  };
  const data = { files: options.files || [inferenceChange], runs: options.runs || [run('ci.yml'), run('inference.yml')],
    forkruns: options.forkRuns || [], checks: options.checks || [], statuses: options.statuses || [], prs: [pr], jobs: options.jobs || inferenceJobs() };
  const github = { rest, paginate: async (method, parameters) => {
    if (options.apiError === method) throw new Error('GitHub API unavailable');
    if (method === 'jobs') jobRequests.push(parameters);
    return structuredClone(data[method]);
  } };
  const context = { repo: { owner: 'Mesh-LLM', repo: 'skippy' }, runId: 42,
    payload: options.payload || { pull_request: pr } };
  await aggregate({ github, context, depot: options.depot, core: { info() {}, setFailed: message => failures.push(message) } });
  assert.ok(calls.every(c => c.sha === sha && c.context === 'PR checks'));
  return { state: calls.at(-1)?.state, calls, failures, jobRequests };
}

test('unlabeled inference PRs require qualification; unrelated jobs may intentionally skip', async () => {
  for (const conclusion of ['success', 'skipped', 'neutral']) {
    const result = await evaluate({ pr: { labels: [] }, runs: [run('ci.yml', 1, { conclusion }), run('inference.yml', 2)] });
    assert.equal(result.state, 'success');
    assert.deepEqual(result.failures, []);
  }
});

test('green skipped plans, skipped matrix rows and incomplete inference cannot satisfy the gate', async () => {
  for (const name of inferenceJobs().map(job => job.name)) {
    for (const conclusion of ['skipped', 'neutral', 'failure', 'cancelled', null]) {
      const jobs = inferenceJobs().map(job => job.name === name ? { ...job, conclusion } : job);
      assert.equal((await evaluate({ jobs })).state, 'failure', `${name}: ${conclusion}`);
    }
    assert.equal((await evaluate({ jobs: inferenceJobs().filter(job => job.name !== name) })).state, 'failure', name);
  }
  for (const conclusion of ['skipped', 'neutral']) {
    assert.equal((await evaluate({ runs: [run('ci.yml'), run('inference.yml', 2, { conclusion })] })).state, 'failure');
  }
});

test('the real green skipped qualification run from recovery PR 201 is rejected', async () => {
  const fixture = require('./fixtures/inference-skipped-plan.json');
  const result = await evaluate({ runs: [run('ci.yml'), run('inference.yml', fixture.run.id, fixture.run)], jobs: fixture.jobs });
  assert.equal(result.state, 'failure');
  assert.match(result.failures[0], /Inference qualification: plan: skipped/);
});

test('only the latest exact-head inference run supplies job evidence', async () => {
  const result = await evaluate({ runs: [run('ci.yml'), run('inference.yml', 2),
    run('inference.yml', 3, { run_attempt: 2 }), run('inference.yml', 4, { head_sha: '2'.repeat(40) })] });
  assert.equal(result.state, 'success');
  assert.deepEqual(result.jobRequests, [{ owner: 'Mesh-LLM', repo: 'skippy', run_id: 3, filter: 'all', per_page: 100 }]);
  assert.equal((await evaluate({ runs: [run('ci.yml'), run('inference.yml', 2, { head_sha: '2'.repeat(40) })] })).state, 'pending');
});

test('partial reruns inherit successful jobs but newer skips or failures override older success', async () => {
  const runs = [run('ci.yml'), run('inference.yml', 2, { run_attempt: 2 })];
  const original = inferenceJobs();
  const failedRow = original.find(job => job.name.startsWith('qualification ('));
  const summary = original.find(job => job.name === 'summary');
  const firstAttempt = original.map(job => [failedRow.name, summary.name].includes(job.name)
    ? { ...job, conclusion: 'failure' } : job);
  const rerun = [failedRow, summary].map(job => ({ ...job, id: job.id + 100, run_attempt: 2 }));
  assert.equal((await evaluate({ runs, jobs: [...rerun, ...firstAttempt] })).state, 'success');
  for (const conclusion of ['skipped', 'failure', 'cancelled']) {
    assert.equal((await evaluate({ runs, jobs: [...original,
      { ...failedRow, id: 100, run_attempt: 2, conclusion }] })).state, 'failure');
  }
  assert.equal((await evaluate({ runs, jobs: [...original,
    { ...failedRow, id: 100, run_attempt: 3 }] })).state, 'failure');
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
  const expectedPaths = ['.github/workflows/ci-images.yml', '.github/workflows/ci-images-pr.yml',
    '.github/workflows/ci-image-builds.yml', 'ci/image-targets.json', 'ci/Dockerfile*',
    'ci/targets.json', 'scripts/ci-preserve-cuda-license.sh', 'rust-toolchain.toml', 'ci/images.just'];
  assert.equal(triggers.length, 2);
  for (const trigger of triggers) assert.deepEqual(JSON.parse('[' + trigger[1].replaceAll("'", '"') + ']'), expectedPaths);
  const pr = readFileSync(join(__dirname, '../workflows/ci-images-pr.yml'), 'utf8');
  assert.equal(pr.match(/paths: \[([^\]]+)\]/)[1], triggers[0][1]);
  for (const filename of [
    'ci/Dockerfile', 'ci/Dockerfile.cuda', 'ci/Dockerfile.gpu', 'ci/Dockerfile.gpu.dockerignore',
    'ci/targets.json', 'scripts/ci-preserve-cuda-license.sh', 'rust-toolchain.toml', 'ci/images.just', '.github/workflows/ci-images.yml'
  ]) {
    assert.equal((await evaluate({ files: [{ filename }] })).state, 'pending');
    assert.equal((await evaluate({ files: [{ filename }], runs: [run('ci.yml'), run('inference.yml', 2), run('ci-images.yml', 3)] })).state, 'success');
  }
  for (const filename of ['.gi/agents/code-review.yaml', 'docs/site/index.mdx']) {
    assert.equal((await evaluate({ files: [{ filename }] })).state, 'success');
  }
  assert.equal((await evaluate({ files: [{ filename: 'archive/old-container', previous_filename: 'ci/Dockerfile' }] })).state, 'pending');
});

test('docs-only pull requests wait only for Documentation, including files renamed out of docs', async () => {
  const normal = [run('ci.yml'), run('inference.yml', 2)];
  for (const file of [{ filename: 'docs-site/index.mdx' },
    { filename: 'archive/index.mdx', previous_filename: 'docs-site/index.mdx' }]) {
    assert.equal((await evaluate({ files: [file], runs: [] })).state, 'pending');
    assert.equal((await evaluate({ files: [file], runs: [run('docs.yml', 3, { event: 'push' })] })).state, 'pending');
    assert.equal((await evaluate({ files: [file], runs: [run('docs.yml')] })).state, 'success');
    assert.equal((await evaluate({ files: [file], runs: [run('docs.yml', 3, { conclusion: 'failure' })] })).state, 'failure');
    assert.equal((await evaluate({ files: [file, inferenceChange], runs: normal })).state, 'pending');
    assert.equal((await evaluate({ files: [file, inferenceChange], runs: [...normal, run('docs.yml', 3)] })).state, 'success');
  }
});

test('native trigger patterns distinguish metadata, code-only, inference and mixed changes', async () => {
  const cases = [
    ['README.md', []],
    ['crates/skippy-cli/.config/README.md', []],
    ['crates/skippy-cli/docs/reference.rs', []],
    ['crates/skippy-cli/tests/smoke.rs', ['ci.yml']],
    ['crates/skippy-cli/src/helper_tests.rs', ['ci.yml']],
    ['crates/skippy-cli/src/main.rs', ['ci.yml', 'inference.yml']],
    ['crates/skippy-runtime/src/session.rs', ['ci.yml', 'inference.yml']],
    ['llama_cpp/upstream.txt', ['ci.yml', 'inference.yml']],
    ['llama_cpp/patches/model_support/0024-layout.patch', ['ci.yml', 'inference.yml']],
    ['ci/inference-ladder.json', ['ci.yml', 'inference.yml']],
    ['scripts/inference-execute.py', ['ci.yml', 'inference.yml']],
    ['scripts/lib/inference_inputs.py', ['ci.yml', 'inference.yml']],
    ['scripts/tests/inference_contracts.py', ['ci.yml']],
    ['.gi/templates.yaml', ['ci.yml', 'inference.yml']],
    ['.gi/agents/code-review.yaml', []],
    ['.github/scripts/pr-checks.cjs', ['ci.yml']],
    ['.github/path-filters.yml', ['ci.yml']],
    ['.github/workflows/inference.yml', ['ci.yml']],
    ['.github/workflows/llama-upgrade.yml', ['ci.yml']],
    ['.cargo/config.toml', ['ci.yml', 'inference.yml']],
    ['Justfile', ['ci.yml', 'inference.yml']],
    ['ci/images.just', ['ci.yml', 'ci-images.yml']],
  ];
  for (const [filename, workflows] of cases) {
    assert.deepEqual([...expectedWorkflows([{ filename }]).keys()],
      workflows.map(path => `.github/workflows/${path}`), filename);
    const files = [{ filename }];
    assert.equal((await evaluate({ files, runs: [] })).state, workflows.length ? 'pending' : 'success', filename);
    assert.equal((await evaluate({ files, runs: workflows.map(path => run(path)) })).state, 'success', filename);
  }
});

test('native CI and inference triggers match their independent route lists', () => {
  const lists = text => [...text.matchAll(/^ {2,6}- '([^']+)'$/gm)].map(match => match[1]);
  const config = readFileSync(join(__dirname, '../path-filters.yml'), 'utf8');
  const route = name => lists(config.match(new RegExp(`^${name}:\\n((?:  .+\\n)+)`, 'm'))[1]);
  const automation = route('automation');
  const inference = route('inference');
  const ci = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
  const [prPaths, targetPaths, pushPaths] = ci.split(/^  (?:pull_request_target|push):\n/m);
  const spark = readFileSync(join(__dirname, '../workflows/inference.yml'), 'utf8');
  assert.deepEqual(lists(pushPaths.split(/^  schedule:\n/m)[0]), automation);
  assert.deepEqual(lists(spark.split(/^  schedule:\n/m)[0]), inference);
  assert.deepEqual(lists(prPaths), automation);
  assert.deepEqual(lists(targetPaths), automation);
  const depotPr = readFileSync(join(__dirname, '../workflows/ci-pr.yml'), 'utf8');
  assert.deepEqual(lists(depotPr), automation);
  const includes = automation.filter(path => !path.startsWith('!'));
  for (const group of ['code', 'packages', 'inference']) {
    for (const path of route(group).filter(path => !path.startsWith('!'))) {
      assert.ok(includes.some(trigger => trigger === path ||
        (trigger.endsWith('/**') && path.startsWith(trigger.slice(0, -2)))), `CI never triggers for ${path}`);
    }
  }
  const mainIncludes = lists(pushPaths.split(/^  schedule:\n/m)[0]).filter(path => !path.startsWith('!'));
  for (const name of ['packaging', 'native']) {
    for (const path of route(name).filter(path => !path.startsWith('!'))) {
      assert.ok(mainIncludes.some(trigger => trigger === path ||
        (trigger.endsWith('/**') && path.startsWith(trigger.slice(0, -2)))), `Main CI never triggers for ${name}: ${path}`);
    }
  }
});

test('same-repository PRs to any base wait for the protected CI producer', async () => {
  const pr = { head: { sha, repo: { id: 42 } }, base: { ref: 'codex/native-decode-port', repo: { id: 42 } } };
  const ordinary = run('ci.yml', 1, { event: 'pull_request' });
  const protectedCI = run('ci.yml', 3, { event: 'pull_request_target' });
  const inference = run('inference.yml', 2);
  assert.equal((await evaluate({ pr, depot: true, runs: [ordinary, inference] })).state, 'pending');
  assert.equal((await evaluate({ pr, depot: true, runs: [ordinary, inference, protectedCI] })).state, 'success');
  const fork = { ...pr, head: { ...pr.head, repo: { id: 43 } } };
  const forkCI = run('ci-pr.yml', 4);
  assert.equal((await evaluate({ pr: fork, depot: true, runs: [protectedCI, inference] })).state, 'pending');
  assert.equal((await evaluate({ pr: fork, depot: true, runs: [forkCI, inference] })).state, 'pending');
});

test('Depot PR checks require each protected producer rather than its legacy run', async () => {
  const files = [inferenceChange, { filename: 'ci/Dockerfile' }, { filename: 'docs-site/index.mdx' }];
  const legacy = [run('ci.yml'), run('inference.yml'), run('ci-images.yml'), run('docs.yml')];
  const protectedRuns = [run('ci.yml'), run('inference.yml'), run('ci-images-pr.yml'), run('docs-pr.yml')];
  assert.equal((await evaluate({ files, depot: true, runs: legacy })).state, 'pending');
  assert.equal((await evaluate({ files, depot: true, runs: protectedRuns })).state, 'success');
  assert.equal((await evaluate({ files, depot: true, runs: protectedRuns.slice(0, -1) })).state, 'pending');
  assert.equal((await evaluate({ files, runs: legacy })).state, 'success');

  const pr = { head: { sha, repo: { id: 42 } }, base: { ref: 'main', repo: { id: 42 } } };
  const sameRepository = [run('ci.yml', 3, { event: 'pull_request_target' }), ...protectedRuns.slice(1)];
  assert.equal((await evaluate({ files, pr, depot: true, runs: sameRepository })).state, 'success');
});

test('latest run and attempt govern reruns; old cancellations do not poison new results', async () => {
  const runs = [run('ci.yml'), run('inference.yml', 2, { conclusion: 'cancelled' }), run('inference.yml', 3)];
  assert.equal((await evaluate({ runs })).state, 'success');
  runs.push(run('inference.yml', 4, { status: 'in_progress', conclusion: null, run_attempt: 2 }));
  assert.equal((await evaluate({ runs })).state, 'pending');
  runs[3].status = 'completed'; runs[3].conclusion = 'failure';
  assert.equal((await evaluate({ runs })).state, 'failure');
});

test('other sources, other PRs, different triggers and the controller do not satisfy missing work', async () => {
  for (const extra of [{ head_sha: '2'.repeat(40) }, { pull_requests: [{ number: 53 }] }, { event: 'push' }, { event: 'pull_request' }]) {
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
  for (const apiError of ['files', 'runs', 'jobs', 'checks', 'statuses']) {
    const result = await evaluate({ apiError });
    assert.equal(result.state, 'failure');
    assert.equal(result.failures.length, 1);
  }
  const truncated = await evaluate({ pr: { changed_files: 2 }, files: [{ filename: 'README.md' }], runs: [] });
  assert.equal(truncated.state, 'failure');
  assert.match(truncated.failures[0], /incomplete PR file list/);
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

test('acceptance criteria block otherwise successful checks and pending workflows', async () => {
  for (const invalid of [null, '', '## Acceptance criteria\n', body.replace('- [x]', '- [ ]')]) {
    for (const runs of [[], [run('ci.yml'), run('inference.yml')]]) {
      const result = await evaluate({ pr: { body: invalid }, runs });
      assert.equal(result.state, 'failure');
      assert.match(result.failures[0], /Acceptance criteria/);
    }
  }
});

test('live description governs edits, including an edit while workflows are inspected', async () => {
  const payload = { action: 'edited', pull_request: { number: 52, head: { sha }, body: '' } };
  assert.equal((await evaluate({ payload })).state, 'success');
  const current = { state: 'open', head: { sha }, body: body.replace('- [x]', '- [ ]') };
  const result = await evaluate({ current });
  assert.equal(result.state, 'failure');
  assert.match(result.failures[0], /Acceptance criteria incomplete/);
});

test('description edits trigger the protected metadata-only controller', () => {
  const workflow = readFileSync(join(__dirname, '../workflows/pr-checks.yml'), 'utf8');
  assert.match(workflow, /pull_request_target:\n\s+types: \[[^\]]*\bedited\b/);
  assert.match(workflow, /ref: main/);
});

const forkPR = { head: { sha, repo: { id: 43 } }, base: { repo: { id: 42 } } };
const forkRun = (extra = {}) => run('fork-native.yml', 100, {
  event: 'workflow_run', head_sha: '2'.repeat(40), display_title: sha, ...extra,
});
const forkJobs = () => [{ id: 99, name: 'admission', run_attempt: 1, status: 'completed', conclusion: 'success' },
  { id: 100, name: 'ci / CI', run_attempt: 1, status: 'completed', conclusion: 'success' },
  ...inferenceJobs().map(job => ({ ...job, name: 'inference / ' + job.name }))];
const forkOptions = () => ({ pr: forkPR, runs: [run('ci.yml', 1, { event: 'pull_request' })],
  forkRuns: [forkRun()], jobs: forkJobs() });

test('a fork cannot green itself with hosted CI alone; exact-source protected native results are required', async () => {
  assert.equal((await evaluate(forkOptions())).state, 'success');
  assert.equal((await evaluate({ ...forkOptions(), forkRuns: [] })).state, 'pending');
  assert.equal((await evaluate({ ...forkOptions(), forkRuns: [forkRun({ display_title: '3'.repeat(40) })] })).state, 'pending');
  assert.equal((await evaluate({ ...forkOptions(), forkRuns: [forkRun({ event: 'pull_request' })] })).state, 'pending');
  for (const status of ['queued', 'waiting', 'in_progress']) {
    assert.equal((await evaluate({ ...forkOptions(), forkRuns: [forkRun({ status })] })).state, 'pending');
  }
});

test('fork admission, CI gate and every protected inference job must actually succeed', async () => {
  for (const required of forkJobs()) {
    for (const conclusion of ['skipped', 'failure', 'cancelled', 'neutral']) {
      const jobs = forkJobs().map(job => job.name === required.name ? { ...job, conclusion } : job);
      assert.equal((await evaluate({ ...forkOptions(), jobs })).state, 'failure', `${required.name}: ${conclusion}`);
    }
    assert.equal((await evaluate({ ...forkOptions(), jobs: forkJobs().filter(job => job.name !== required.name) })).state, 'failure');
  }
  for (const conclusion of ['skipped', 'neutral', 'failure', 'cancelled']) {
    assert.equal((await evaluate({ ...forkOptions(), forkRuns: [forkRun({ conclusion })] })).state, 'failure');
  }
});

test('protected fork completion wakes the aggregate for the candidate, not main, and latest reruns govern', async () => {
  assert.equal((await evaluate({ ...forkOptions(), payload: { workflow_run: forkRun({ pull_requests: [] }) } })).state, 'success');
  assert.equal((await evaluate({ ...forkOptions(), forkRuns: [forkRun(), forkRun({ id: 101, conclusion: 'failure' })] })).state, 'failure');
  assert.equal((await evaluate({ ...forkOptions(), jobs: [...forkJobs(),
    { ...forkJobs()[1], id: 200, run_attempt: 2, conclusion: 'skipped' }], forkRuns: [forkRun({ run_attempt: 2 })] })).state, 'failure');
});

test('a fork run held for maintainer approval is pending, not failed (real #222 context)', async () => {
  const fork = { head: { sha, repo: { id: 7 } }, base: { repo: { id: 42 } } };
  const held = run('ci.yml', 1, { event: 'pull_request', conclusion: 'action_required' });
  const result = await evaluate({ pr: fork, runs: [held] });
  assert.equal(result.state, 'pending');
  assert.equal(result.calls.at(-1).description, 'Waiting for a maintainer to approve workflows');
  assert.deepEqual(result.failures, []);
  // A real failure beside the held run still blocks.
  const failed = await evaluate({ pr: fork, runs: [held, run('docs.yml', 2, { conclusion: 'failure' })] });
  assert.equal(failed.state, 'failure');
});

test('base Depot configuration does not change any fork producer (real #218 context)', async () => {
  const files = [inferenceChange, { filename: 'ci/Dockerfile' }, { filename: 'docs-site/index.mdx' }];
  for (const depot of [false, true]) {
    const expected = expectedWorkflows(files, false, depot);
    assert.deepEqual([...expected.keys()].sort(), [
      '.github/workflows/ci.yml', '.github/workflows/ci-images.yml',
      '.github/workflows/docs.yml', '.github/workflows/fork-native.yml',
    ].sort());
    const runs = [...forkOptions().runs, run('ci-images.yml'), run('docs.yml')];
    assert.equal((await evaluate({ ...forkOptions(), depot, files, runs })).state, 'success');
    assert.equal((await evaluate({ ...forkOptions(), depot, files,
      runs: [run('ci-pr.yml'), run('ci-images-pr.yml'), run('docs-pr.yml')] })).state, 'pending');
  }
});
