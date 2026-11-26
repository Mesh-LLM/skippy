const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const workflow = name => readFileSync(join(__dirname, '../workflows', name), 'utf8');
const jobs = text => text.split(/^jobs:\n/m)[1].split(/(?=^  [\w-]+:\n)/m).filter(Boolean);
const job = (text, name) => jobs(text).find(item => item.startsWith(`  ${name}:\n`));
const permissions = text => [...text.matchAll(/^    permissions:\n((?:      [\w-]+: \w+\n)+)/gm)]
  .flatMap(match => [...match[1].matchAll(/^      ([\w-]+): (\w+)$/gm)].map(([, name, access]) => [name, access]));

test('all three full-CI calls grant the reusable CI read permissions', () => {
  const required = permissions(workflow('ci.yml'));
  assert.ok(required.length > 0);
  const callers = jobs(workflow('llama-upgrade.yml')).filter(item => item.includes('uses: ./.github/workflows/ci.yml'));
  assert.equal(callers.length, 3);
  for (const caller of callers) {
    const granted = Object.fromEntries(permissions(caller));
    for (const [name, access] of required) {
      assert.equal(access, 'read');
      assert.equal(granted[name], access);
    }
    assert.match(caller, /profile: full/);
  }
});

test('each later repair consumes the latest candidate and only its failed CI round', () => {
  const controller = workflow('llama-upgrade.yml');
  const first = job(controller, 'repair');
  const second = job(controller, 'repair_2');
  assert.match(first, /needs\.qualify\.result == 'failure'/);
  assert.match(first, /candidate_artifact: llama-initial-candidate/);
  assert.match(first, /ci_prefix: 'qualify \/ '/);
  assert.match(second, /needs\.requalify\.result == 'failure'/);
  assert.match(second, /candidate_artifact: llama-repaired-candidate/);
  assert.match(second, /ci_prefix: 'requalify \/ '/);
  assert.match(job(controller, 'requalify'), /needs\.repair\.outputs\.candidate_sha != ''/);
  assert.match(job(controller, 'requalify_2'), /needs\.repair_2\.outputs\.candidate_sha != ''/);
  assert.match(job(controller, 'requalify_2'), /candidate_artifact: llama-repaired-2-candidate/);
  assert.doesNotMatch(controller, /^  (repair_3|requalify_3):/m);
  assert.doesNotMatch(controller, /family-256g|upgrade certify|upgrade dgx|JIANYANG_INFERENCE_SSH_KEY/);
  const publish = job(controller, 'publish');
  assert.match(publish, /needs\.result\.result == 'success'/);
  assert.match(publish, /VALIDATED_SHA: \$\{\{ needs\.result\.outputs\.candidate_sha \}\}/);
  assert.match(publish, /--validated-sha "\$VALIDATED_SHA"/);
});

test('preparation and the shared repair implementation keep Goose on native JY', () => {
  const controller = workflow('llama-upgrade.yml');
  const repair = workflow('llama-upgrade-repair.yml');
  for (const definition of [job(controller, 'candidate'), job(repair, 'repair')]) {
    assert.match(definition, /group: jy-rnr_vfnz8k5p4yrh334/);
    assert.match(definition, /labels: \[self-hosted, jy-linux-docker, Linux\]/);
    assert.match(definition, /timeout-minutes: 1430/);
    assert.doesNotMatch(definition, /depot-|container:|install-gh/);
    assert.match(definition, /run: gh --version/);
    assert.match(definition, /GOOSE_MODE: auto/);
    assert.match(definition, /run: just automation-run upgrade runner-check/);
    const fallback = definition.match(/SKIPPY_UPGRADE_REPAIR_COMMAND: .*\|\| '([^']+)'/);
    assert.deepEqual(JSON.parse(fallback[1]), ['goose', 'run', '--with-builtin', 'developer', '--text']);
  }
  for (const name of ['repair', 'repair_2']) {
    assert.match(job(controller, name), /uses: .\/\.github\/workflows\/llama-upgrade-repair.yml/);
  }
});

test('infrastructure failures stop source repair and all repair evidence is retained', () => {
  const repair = workflow('llama-upgrade-repair.yml');
  assert.match(repair, /if: steps\.failure\.outputs\.repairable == 'true'/);
  assert.match(repair, /--ci-prefix "\$CI_PREFIX"/);
  assert.match(repair, /pattern: \$\{\{ inputs\.ci_scope \}\}-\*-evidence/);
  assert.match(repair, /if: \$\{\{ always\(\) \}\}\n        with:\n          name: \$\{\{ inputs\.output_artifact \}\}-recovery/);
  assert.match(repair, /target\/llama-upgrade\/diagnostics/);
  assert.match(repair, /target\/llama-repair\/diagnostics/);
});
