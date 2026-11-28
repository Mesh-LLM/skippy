const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

// This trusted workflow's expression uses the shared JS/Actions boolean subset.
// Evaluate the actual YAML condition so removing its status function reproduces
// GitHub's implicit success() dependency guard, including skipped dependencies.
const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
const expression = workflow.match(/\n  quality:\n[\s\S]*?\n    if: >-\n((?:      .+\n)+)/)?.[1].trim();
assert.ok(expression, 'quality must declare its explicit multiline dependency condition');
const condition = new Function('needs', 'cancelled', `return (${expression});`);

function eligible({ automation = 'false', automationResult = 'skipped',
  changes = 'success', plan = 'success', code = 'true', cancelled = false } = {}) {
  const needs = {
    changes: { result: changes, outputs: { automation, code } },
    plan: { result: plan }, automation: { result: automationResult },
  };
  const explicitStatus = /\b(?:cancelled|always|success|failure)\s*\(/.test(expression);
  const implicitSuccess = Object.values(needs).every(job => job.result === 'success');
  return (explicitStatus || implicitSuccess) && condition(needs, () => cancelled);
}

test('code checks run when unrelated automation was intentionally skipped', () => {
  assert.equal(eligible(), true);
  assert.equal(eligible({ automation: 'true', automationResult: 'success' }), true);
});

test('failed, cancelled or unexpectedly skipped prerequisites never admit quality', () => {
  for (const result of ['failure', 'cancelled', 'skipped']) {
    assert.equal(eligible({ automation: 'true', automationResult: result }), false);
  }
  assert.equal(eligible({ changes: 'failure' }), false);
  assert.equal(eligible({ plan: 'failure' }), false);
  assert.equal(eligible({ plan: 'skipped' }), false);
  assert.equal(eligible({ cancelled: true }), false);
  assert.equal(eligible({ code: 'false' }), false);
});
