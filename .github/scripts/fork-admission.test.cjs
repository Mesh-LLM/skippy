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
  await admission({ depot: true, run: { path: '.github/workflows/ci-pr.yml' } });
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
  await assert.rejects(admission({ depot: true }), /Not a successful/);
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
  assert.match(workflow, /workflow_run:\n +workflows: \[CI, CI PR\]/);
  assert.match(workflow, /ref: main/);
  assert.doesNotMatch(workflow, /download-artifact|secrets: inherit|environment:/);
  assert.match(workflow, /uses: \.\/\.github\/workflows\/ci.yml/);
  assert.match(workflow, /uses: \.\/\.github\/workflows\/inference.yml/);
  const ci = workflow.split('\n  ci:\n')[1].split('\n  inference:\n')[0];
  assert.doesNotMatch(ci, /secrets|: write/);
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
