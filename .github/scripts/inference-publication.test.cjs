const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const workflow = readFileSync(join(__dirname, '../workflows/inference.yml'), 'utf8');
const summary = workflow.split('\n  summary:\n')[1].split('\n  report:\n')[0];
const script = summary.split('          script: |\n')[1].replace(/^ {12}/gm, '');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const evaluate = new AsyncFunction('process', 'github', 'context', 'core', script);

async function status(eventName, publication, overrides = {}) {
  const statuses = [];
  const failures = [];
  const process = { env: {
    SOURCE_SHA: 'a'.repeat(40), PLAN: 'success', RESULT: 'success',
    PUBLICATION_REQUIRED: 'true', PUBLICATION: publication,
    RUN_URL: 'https://example.invalid/run', ...overrides,
  } };
  const github = { rest: { repos: {
    createCommitStatus: async value => statuses.push(value),
  } } };
  await evaluate(process, github, { eventName, repo: { owner: 'Mesh-LLM', repo: 'skippy' } },
    { setFailed: message => failures.push(message) });
  assert.equal(statuses.length, 1);
  return { state: statuses[0].state, failures };
}

test('scheduled and manual nightly success requires successful publication', async () => {
  for (const event of ['schedule', 'workflow_dispatch']) {
    assert.deepEqual(await status(event, 'success'), { state: 'success', failures: [] });
    for (const publication of ['skipped', 'failure', 'cancelled', '']) {
      const result = await status(event, publication);
      assert.equal(result.state, 'failure', `${event}: ${publication}`);
      assert.equal(result.failures.length, 1);
    }
  }
});

test('PR qualification and intentional opt-out retain their existing status contract', async () => {
  assert.deepEqual(await status('pull_request_target', 'skipped', { PUBLICATION_REQUIRED: 'false' }),
    { state: 'success', failures: [] });
  assert.deepEqual(await status('pull_request_target', 'skipped', {
    PUBLICATION_REQUIRED: '', PLAN: 'skipped', RESULT: 'skipped',
  }), { state: 'success', failures: [] });
});

test('publisher runs after qualification failures when publication was admitted', () => {
  const publisher = workflow.split('\n  nightly_publish:\n')[1].split('\n  summary:\n')[0];
  const condition = publisher.match(/    if: >-\n([\s\S]+?)\n    runs-on:/)[1]
    .replace('always()', 'true').replace('cancelled()', 'cancelled');
  const shouldPublish = new Function('needs', 'cancelled', `return (${condition})`);
  for (const qualification of ['success', 'failure', 'cancelled']) {
    const needs = { plan: { result: 'success', outputs: { publication: 'true' } },
      qualification: { result: qualification } };
    assert.equal(shouldPublish(needs, false), true);
    assert.equal(shouldPublish(needs, true), false);
    needs.plan.outputs.publication = 'false';
    assert.equal(shouldPublish(needs, false), false);
  }
  assert.equal(shouldPublish({ plan: { result: 'failure', outputs: { publication: 'true' } } }, false), false);
});
