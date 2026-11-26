const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const workflow = readFileSync(join(__dirname, '../workflows/inference.yml'), 'utf8');
const github = { event_name: 'schedule', ref: 'refs/heads/main', repository: 'Mesh-LLM/skippy' };
const steps = profile => ({ admission: { outputs: { profile } } });
const needs = (profile, result = 'success') => ({ plan: { result, outputs: { profile } } });

test('scheduled Release calls do not select nightly smoke expansion or public metrics collection', () => {
  const flags = [...workflow.matchAll(/^ +NIGHTLY_PUBLICATION: \$\{\{ (.+) \}\}$/gm)];
  assert.equal(flags.length, 2);
  for (const [, expression] of flags) {
    const enabled = new Function('github', 'steps', 'needs', `return (${expression});`);
    assert.equal(enabled(github, steps('release'), needs('release')), false);
    assert.equal(enabled(github, steps('nightly'), needs('nightly')), true);
    assert.equal(enabled({ ...github, event_name: 'workflow_dispatch' }, steps('nightly'), needs('nightly')), false);
    assert.equal(enabled({ ...github, ref: 'refs/heads/branch' }, steps('nightly'), needs('nightly')), false);
    assert.equal(enabled({ ...github, repository: 'Elsewhere/skippy' }, steps('nightly'), needs('nightly')), false);
  }
});

test('public report publication belongs only to admitted scheduled nightly runs', () => {
  const section = workflow.split('\n  nightly_publish:\n')[1].split('\n  summary:\n')[0];
  const condition = section.match(/    if: >-\n([\s\S]+?)\n    runs-on:/)[1];
  const publish = new Function('github', 'needs', 'always', 'cancelled', `return (${condition});`);
  assert.equal(publish(github, needs('release'), () => true, () => false), false);
  assert.equal(publish(github, needs('nightly'), () => true, () => false), true);
  assert.equal(publish(github, needs('nightly', 'failure'), () => true, () => false), false);
  assert.equal(publish(github, needs('nightly'), () => true, () => true), false);
});

const summary = workflow.split('\n  summary:\n')[1].split('\n  report:\n')[0];
const script = summary.split('          script: |\n')[1].replace(/^ {12}/gm, '');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const evaluate = new AsyncFunction('process', 'context', 'github', 'core', script);

async function status(profile, publication, result = 'success', plan = 'success', eventName = 'schedule') {
  const statuses = [], failures = [];
  await evaluate({ env: {
    SOURCE_SHA: 'a'.repeat(40), PROFILE: profile, PLAN: plan, RESULT: result,
    PUBLICATION: publication, RUN_URL: 'https://example.invalid/run',
  } }, { eventName, repo: { owner: 'Mesh-LLM', repo: 'skippy' } }, {
    rest: { repos: { createCommitStatus: async value => statuses.push(value) } },
  }, { setFailed: message => failures.push(message) });
  return { state: statuses[0].state, failures };
}

test('scheduled release success does not require nightly publication, while nightly success does', async () => {
  assert.match(summary, /PROFILE: \$\{\{ needs.plan.outputs.profile \}\}/);
  assert.deepEqual(await status('release', 'skipped'), { state: 'success', failures: [] });
  assert.deepEqual(await status('nightly', 'success'), { state: 'success', failures: [] });
  assert.equal((await status('nightly', 'skipped')).state, 'failure');
  assert.equal((await status('release', 'skipped', 'failure')).state, 'failure');
});

test('matching PRs always plan qualification without a label or label-change event', () => {
  const trigger = workflow.split('\n  schedule:\n')[0];
  assert.match(trigger, /types: \[opened, synchronize, reopened, ready_for_review\]/);
  const plan = workflow.split('\n  plan:\n')[1].split('\n  qualification:\n')[0];
  assert.match(plan, /if: github.event_name != 'pull_request_target' \|\| github.event.pull_request.head.repo.id == github.event.repository.id/);
  assert.doesNotMatch(plan, /qualify|labels/);
  assert.match(plan, /run: just inference-admit/);
  assert.match(plan, /just inference-matrix/);
});

test('PR summary requires successful admission and matrix; skipped plans cannot report qualification', async () => {
  assert.equal((await status('pr', 'skipped', 'success', 'success', 'pull_request_target')).state, 'success');
  for (const result of ['skipped', 'failure', 'cancelled', '']) {
    for (const [plan, matrix] of [[result, 'success'], ['success', result]]) {
      const outcome = await status('pr', 'skipped', matrix, plan, 'pull_request_target');
      assert.equal(outcome.state, 'failure', `${plan}/${matrix}`);
      assert.equal(outcome.failures.length, 1);
    }
  }
});

test('ungated fork target events cannot publish a failing qualification status before approval', () => {
  assert.match(summary, /if: always\(\) && needs.plan.result != 'skipped'/);
});
