const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { checkCacheWriteDenied } = require('./depot-cache-canary.cjs');

const env = {
  RUNNER_NAME: 'depot-test',
  ACTIONS_CACHE_MODE: 'read',
  ACTIONS_CACHE_SERVICE_V2: 'true',
  ACTIONS_RESULTS_URL: 'http://127.0.0.1:8123/cache/',
  ACTIONS_RUNTIME_TOKEN: 'job-token',
  GITHUB_RUN_ID: '123',
  GITHUB_RUN_ATTEMPT: '2',
};

test('requires an explicit service denial with the unmodified job token', async () => {
  const response = await checkCacheWriteDenied(env, async (url, request) => {
    assert.equal(url.origin, 'http://127.0.0.1:8123');
    assert.equal(url.pathname, '/twirp/github.actions.results.api.v1.CacheService/CreateCacheEntry');
    assert.equal(request.method, 'POST');
    assert.equal(request.headers.Authorization, 'Bearer job-token');
    assert.equal(request.redirect, 'error');
    const { key, version } = JSON.parse(request.body);
    assert.equal(key, 'skippy-depot-cache-canary-123-2-write-probe');
    assert.match(version, /^[a-f0-9]{64}$/);
    return Response.json({ code: 'permission_denied' }, { status: 403 });
  });
  assert.equal(response, 403);
  assert.equal(await checkCacheWriteDenied(env, async () =>
    Response.json({ ok: false, message: 'cache write denied: read-only' })), 200);
});

test('uses the runner-provided HTTP or HTTPS origin without forwarding URL credentials', async () => {
  for (const protocol of ['http:', 'https:']) {
    const ACTIONS_RESULTS_URL = `${protocol}//name:password@cache.example.test:8123/path?query=1#fragment`;
    const status = await checkCacheWriteDenied({ ...env, ACTIONS_RESULTS_URL }, async (url, request) => {
      assert.equal(url.href, `${protocol}//cache.example.test:8123/twirp/github.actions.results.api.v1.CacheService/CreateCacheEntry`);
      assert.equal(request.headers.Authorization, 'Bearer job-token');
      return Response.json({ code: 'permission_denied' }, { status: 403 });
    });
    assert.equal(status, 403);
  }
});

test('a successful reservation or inconclusive response fails the canary', async () => {
  await assert.rejects(checkCacheWriteDenied(env, async () => Response.json({ ok: true })), /accepted a write/);
  for (const response of [
    Response.json({ ok: false, message: 'conflict' }),
    new Response('Forbidden', { status: 403 }),
    new Response('unavailable', { status: 503 }),
  ]) {
    await assert.rejects(checkCacheWriteDenied(env, async () => response), /recognized cache write denial/);
  }
});

test('skips requests in the wrong mode or with an unsafe endpoint', async () => {
  const noRequest = () => assert.fail('request must be skipped');
  await assert.rejects(checkCacheWriteDenied({ ...env, ACTIONS_CACHE_MODE: 'write' }, noRequest), /effective cache-mode/);
  await assert.rejects(checkCacheWriteDenied({ ...env, DEPOT_CACHE_TOKEN: 'separate-token' }, noRequest), /separate cache connection/);
  await assert.rejects(checkCacheWriteDenied({ ...env, SCCACHE_WEBDAV_ENDPOINT: 'https://cache.depot.dev' }, noRequest), /separate cache connection/);
  for (const ACTIONS_RESULTS_URL of [
    'invalid',
    'file:///tmp/cache',
    'ftp://cache.example.test/',
  ]) {
    await assert.rejects(checkCacheWriteDenied({ ...env, ACTIONS_RESULTS_URL }, noRequest), /invalid Actions Cache v2 endpoint/);
  }
});

test('the canary is restricted to manual runs on protected main', () => {
  const workflow = readFileSync(join(__dirname, '../workflows/depot-cache.yml'), 'utf8');
  assert.match(workflow, /^on:\n  workflow_dispatch:\npermissions: \{\}$/m);
  assert.match(workflow, /github.workflow_ref == 'Mesh-LLM\/skippy\/\.github\/workflows\/depot-cache\.yml@refs\/heads\/main'/);
  const [seed, read] = workflow.split('\n  depot_cache_read:\n');
  assert.match(seed, /^    cache-mode: write$/m);
  assert.match(read, /^    needs: depot_cache_seed$/m);
  assert.match(read, /^    cache-mode: read$/m);
});
