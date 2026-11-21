const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const workflow = name => readFileSync(join(__dirname, '../workflows', name), 'utf8');
const jobs = text => text.split(/^jobs:\n/m)[1].split(/(?=^  [\w-]+:\n)/m).filter(Boolean);
const permissions = text => [...text.matchAll(/^    permissions:\n((?:      [\w-]+: \w+\n)+)/gm)]
  .flatMap(match => [...match[1].matchAll(/^      ([\w-]+): (\w+)$/gm)]
    .map(([, name, access]) => [name, access]));

test('both llama upgrade CI calls grant the read permissions requested by reusable CI', () => {
  // GitHub rejects the entire workflow before any job starts if a callee
  // requests a permission omitted from its caller's explicit permission map.
  const required = permissions(workflow('ci.yml'));
  assert.ok(required.length > 0);
  const callers = jobs(workflow('llama-upgrade.yml'))
    .filter(job => job.includes('uses: ./.github/workflows/ci.yml'));
  assert.equal(callers.length, 2);
  for (const job of callers) {
    const granted = Object.fromEntries(permissions(job));
    for (const [name, access] of required) {
      assert.equal(access, 'read', `unexpected write permission in qualification: ${name}`);
      assert.equal(granted[name], access, `${job.split('\n')[0].trim()} lacks ${name}: ${access}`);
    }
    assert.ok(Object.values(granted).every(access => access === 'read'));
  }
});

test('resolve runs read-only on hosted Ubuntu before allocating the native candidate builder', () => {
  const all = jobs(workflow('llama-upgrade.yml'));
  const resolve = all.find(job => job.startsWith('  resolve:\n'));
  const candidate = all.find(job => job.startsWith('  candidate:\n'));
  assert.ok(resolve);
  assert.match(resolve, /^    if: github.ref == 'refs\/heads\/main'$/m);
  assert.match(resolve, /^    runs-on: ubuntu-24.04$/m);
  assert.deepEqual(Object.fromEntries(permissions(resolve)), { contents: 'read', 'pull-requests': 'read' });
  assert.match(resolve, /uses: \.\/\.github\/actions\/setup\n        with:\n          cache: 'false'/);
  assert.match(resolve, /persist-credentials: false/);
  assert.match(resolve, /GH_TOKEN: \$\{\{ github.token \}\}/);
  assert.match(resolve, /just automation-run upgrade resolve --upstream "\$UPSTREAM_SHA" --force-certify "\$FORCE_CERTIFY"/);
  for (const output of ['upstream_sha', 'changed', 'certify']) {
    assert.ok(resolve.includes(`${output}: \${{ steps.resolve.outputs.${output} }}`));
  }
  assert.match(candidate, /^    needs: resolve$/m);
  assert.match(candidate, /^    if: github.ref == 'refs\/heads\/main' && needs.resolve.outputs.certify == 'true'$/m);
  assert.match(candidate, /group: jy-rnr_vfnz8k5p4yrh334/);
  assert.match(candidate, /labels: \[self-hosted, jy-linux-docker, Linux, X64\]/);
  assert.doesNotMatch(candidate, /steps\.resolve|upgrade resolve|GH_TOKEN/);
  assert.ok(candidate.includes('UPSTREAM_SHA: ${{ needs.resolve.outputs.upstream_sha }}'));
  assert.match(candidate, /just automation-run upgrade prepare "\$UPSTREAM_SHA"/);
  for (const output of ['changed', 'certify']) {
    assert.ok(candidate.includes(`${output}: \${{ needs.resolve.outputs.${output} }}`));
  }
  assert.ok(candidate.includes('candidate_sha: ${{ steps.prepare.outputs.candidate_sha }}'));
});

test('resolution does not bypass candidate or independent qualification before publication', () => {
  const all = jobs(workflow('llama-upgrade.yml'));
  const job = name => all.find(section => section.startsWith(`  ${name}:\n`));
  assert.match(job('candidate_ci'), /^    needs: candidate$/m);
  assert.match(job('candidate_ci'), /^    if: needs.candidate.outputs.certify == 'true'$/m);
  assert.match(job('candidate_families'), /^    needs: \[candidate, candidate_ci\]$/m);
  assert.match(job('independent_ci'), /^    needs: \[candidate, candidate_ci, candidate_families\]$/m);
  assert.match(job('independent_families'), /^    needs: \[candidate, independent_ci\]$/m);
  assert.match(job('publish'), /^    needs: \[candidate, candidate_ci, candidate_families, independent_ci, independent_families\]$/m);
  assert.match(job('publish'), /^    if: needs.candidate.outputs.changed == 'true'$/m);
});
