const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const expectedWorkflows = require('./pr-trigger-paths.cjs');

const config = readFileSync(join(__dirname, '../path-filters.yml'), 'utf8');
const workflow = readFileSync(join(__dirname, '../workflows/ci.yml'), 'utf8');
const snapshots = require('./fixtures/ci-routing-prs.json');

function route(files) {
  return Object.fromEntries(['automation', 'code', 'packages', 'inference'].map(name => {
    const section = config.split(`\n${name}:\n`)[1];
    const patterns = [...section.split(/\n\w+:\n/)[0].matchAll(/^  - '([^']+)'$/gm)]
      .map(([, pattern]) => ({ exclude: pattern.startsWith('!'), regex: glob(pattern.replace(/^!/, '')) }));
    const selected = files.some(file => patterns.some(rule => !rule.exclude && rule.regex.test(file)) &&
      !patterns.some(rule => rule.exclude && rule.regex.test(file)));
    return [name, selected];
  }));
}

function glob(pattern) {
  const regex = pattern.split(/(\*\*\/|\*\*|\*)/).map(part => {
    if (part === '**/') return '(?:[^/]+/)*';
    if (part === '**') return '.*';
    if (part === '*') return '[^/]*';
    return part.replaceAll('.', '\\.');
  }).join('');
  return new RegExp(`^${regex}$`);
}

test('reports and workflow controllers need only offline automation', () => {
  for (const file of [
    'scripts/inference-render-report.py', 'scripts/lib/inference_markdown.py',
    'scripts/lib/inference_charts.py', 'scripts/lib/inference_png.py',
    'scripts/lib/inference_labels.py', 'scripts/lib/inference_mermaid.py',
    'scripts/lib/inference_health.py', 'scripts/lib/inference_health_charts.py',
    'scripts/lib/inference_health_markdown.py',
    'scripts/inference-report.sh', 'scripts/lib/inference-report-baseline.sh',
    'scripts/tests/inference_nightly.py', '.github/scripts/pr-checks.cjs',
    '.github/workflows/ci.yml', '.github/workflows/inference.yml',
    '.github/actions/cache/action.yml', 'ci/automation.just', 'ci/inference-report-requirements.txt',
  ]) {
    assert.deepEqual(route([file]), { automation: true, code: false, packages: false, inference: false }, file);
    assert.deepEqual([...expectedWorkflows([{ filename: file }]).keys()], ['.github/workflows/ci.yml'], file);
  }
});

test('qualification inputs do not rebuild the runtime packages', () => {
  for (const file of [
    'scripts/inference-plan.py', 'scripts/inference-execute.py', 'scripts/inference-dispatch.py',
    'scripts/inference-publish-nightly.py', 'scripts/lib/inference_baselines.py',
    'scripts/lib/inference_checks.py', 'scripts/lib/inference_inputs.py',
    'scripts/lib/jy_qualification.py', '.gi/templates.yaml', 'ci/inference.just', 'ci/inference-ladder.json',
    'ci/inference-artifacts.json', 'ci/inference-quality.json',
  ]) {
    assert.deepEqual(route([file]), { automation: true, code: false, packages: false, inference: true }, file);
    assert.deepEqual([...expectedWorkflows([{ filename: file }]).keys()],
      ['.github/workflows/ci.yml', '.github/workflows/inference.yml'], file);
  }
});

test('runtime and packaging changes still select hardware, including mixed PRs', () => {
  for (const file of [
    'crates/skippy-api/src/lib.rs', 'crates/skippy-cli/src/main.rs',
    'skippy_api/src/context.cpp', 'llama_cpp/patches/core/series',
    'scripts/build-llama.sh', 'scripts/package-native-runtime.sh', 'scripts/package-cli-release.sh',
    'scripts/lib/native_dependencies.py', 'scripts/lib/ci_smoke.py',
    'scripts/ci-product.py', '.github/actions/ci-package/action.yml',
    '.github/actions/ci-smoke/action.yml', 'ci/targets.json', 'Cargo.lock', 'Justfile',
  ]) {
    const selected = route([file]);
    assert.equal(selected.automation, true, file);
    assert.equal(selected.packages, true, file);
    assert.deepEqual(route(['scripts/inference-render-report.py', file]), selected, file);
  }
  for (const file of ['crates/skippy-cli/tests/smoke.rs', 'crates/skippy-cli/src/helper_tests.rs',
    'crates/skippy-runtime/src/test_support/libraries.rs']) {
    assert.deepEqual(route([file]), { automation: true, code: true, packages: false, inference: false }, file);
  }
  for (const file of ['README.md', 'crates/skippy-api/README.md', 'docs/design/proposal.md']) {
    assert.deepEqual(route([file]), { automation: false, code: false, packages: false, inference: false }, file);
  }
});

test('recent PR snapshots distinguish reporting from real package inputs', () => {
  const pr = number => snapshots.find(pr => pr.number === number).files;
  assert.deepEqual(route(pr(165)), { automation: true, code: false, packages: false, inference: true });
  // Historical PRs edited root recipes. Those edits remain conservative;
  // the same automation recipes now live in their own imported file.
  for (const number of [121, 124]) {
    assert.equal(route(pr(number)).packages, true, `PR #${number}: root Justfile`);
    const migrated = pr(number).map(file => file === 'Justfile' ? 'ci/automation.just' : file);
    assert.equal(route(migrated).packages, false, `PR #${number}: imported automation recipes`);
    assert.equal(route(migrated).automation, true);
    assert.equal(route(migrated).inference, true);
  }
  const metalQualification = pr(122).map(file => file === 'Justfile' ? 'ci/automation.just' : file);
  assert.equal(route(metalQualification).packages, true, 'PR #122 also changes ci_smoke.py');
});

test('CI selects package jobs independently of qualification and preserves full validation', () => {
  const outputs = workflow.split('    outputs:\n')[1].split('    steps:\n')[0];
  const decide = (name, eventName, paths, verified = false, fork = false) => {
    const expression = outputs.match(new RegExp(`^      ${name}: \\$\\{\\{ (.+) \\}\\}$`, 'm'))[1];
    return new Function('github', 'steps', `return (${expression})`)(
      { event_name: eventName, event: { repository: { id: 42 },
        pull_request: eventName.startsWith('pull_request') ? { head: { repo: { id: fork ? 43 : 42 } } } : null } },
      { release: { outputs: { verified: String(verified) } },
        paths: { outputs: Object.fromEntries(Object.entries(paths).map(([key, value]) => [key, String(value)])) } });
  };
  const reporting = route(['scripts/inference-render-report.py']);
  const qualification = route(['ci/inference-quality.json']);
  const runtime = route(['crates/skippy-api/src/lib.rs']);
  for (const paths of [reporting, qualification]) {
    assert.equal(decide('automation', 'pull_request_target', paths), true);
    assert.equal(decide('code', 'pull_request_target', paths), false);
    assert.equal(decide('packages', 'pull_request_target', paths), false);
  }
  assert.equal(decide('packages', 'pull_request_target', runtime), true);
  assert.equal(decide('packages', 'pull_request', runtime), false);
  // A same-repository pull_request run (base other than main) keeps its hosted checks.
  assert.equal(decide('automation', 'pull_request', runtime), true);
  assert.equal(decide('code', 'pull_request', runtime), true);
  // A fork's own run is only the approval gate; the approved-fork controller
  // runs the canary once on protected runners (#203 ran it twice, 19.5 min cold).
  for (const name of ['automation', 'code', 'packages', 'native']) {
    assert.equal(decide(name, 'pull_request', runtime, false, true), false, name);
  }
  for (const event of ['push', 'schedule', 'workflow_dispatch', 'workflow_call']) {
    assert.equal(decide('automation', event, {}), true, event);
    assert.equal(decide('code', event, {}), true, event);
    assert.equal(decide('packages', event, {}), false, event);
  }
  for (const name of ['automation', 'code', 'packages', 'native']) {
    assert.equal(decide(name, 'push', {}, true), false, name);
  }
});


test('audited PRs separate automation recipes and test support from native inputs', () => {
  const audited = require('./fixtures/hardware-routing-prs.json');
  const pr = number => audited.find(pr => pr.number === number).files;
  const recipes = pr(252).map(file => file === 'Justfile' ? 'ci/inference.just' : file);
  assert.deepEqual(route(pr(252)), { automation: true, code: true, packages: true, inference: true });
  assert.deepEqual(route(recipes), { automation: true, code: false, packages: false, inference: true });
  assert.deepEqual(route(pr(241)), { automation: true, code: true, packages: false, inference: false });
  // Inline Rust tests and Windows edits inside shared files remain conservative.
  for (const number of [240, 248, 192, 193]) {
    assert.equal(route(pr(number)).packages, true, `PR #${number}`);
    assert.equal(route(pr(number)).inference, true, `PR #${number}`);
  }
});

test('protected fork routes and aggregate triggers agree for exclusions, mixed changes and renames', () => {
  const files = [
    'crates/skippy-runtime/src/test_support/libraries.rs',
    'scripts/lib/inference_labels.py', 'scripts/lib/inference_health.py',
    'ci/inference.just', 'crates/skippy-runtime/src/session.rs',
  ];
  for (const filename of files) {
    assert.deepEqual(expectedWorkflows.routes([{ filename }]), route([filename]), filename);
    assert.equal(expectedWorkflows([{ filename }]).has('.github/workflows/inference.yml'),
      route([filename]).inference, filename);
    const fork = expectedWorkflows([{ filename }], false);
    assert.equal(fork.has('.github/workflows/fork-native.yml'), true, filename);
  }
  const report = 'scripts/lib/inference_labels.py';
  const runtime = 'crates/skippy-runtime/src/session.rs';
  assert.deepEqual(route([report, runtime]), route([runtime]));
  // Moving code into an excluded directory must still validate the removed runtime path.
  for (const change of [
    { filename: files[0], previous_filename: runtime },
    { filename: runtime, previous_filename: files[0] },
  ]) {
    assert.equal(expectedWorkflows.routes([change]).packages, true);
    assert.equal(expectedWorkflows.routes([change]).inference, true);
    assert.equal(expectedWorkflows([change]).has('.github/workflows/inference.yml'), true);
  }
});
