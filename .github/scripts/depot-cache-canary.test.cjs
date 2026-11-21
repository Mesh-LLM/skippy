const { test } = require('node:test');
const assert = require('node:assert/strict');
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
    assert.equal(url.pathname, '/cache/twirp/github.actions.results.api.v1.CacheService/CreateCacheEntry');
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

test('never sends credentials to a different service or in the wrong mode', async () => {
  const noRequest = () => assert.fail('request must be skipped');
  await assert.rejects(checkCacheWriteDenied({ ...env, ACTIONS_CACHE_MODE: 'write' }, noRequest), /effective cache-mode/);
  await assert.rejects(checkCacheWriteDenied({ ...env, DEPOT_CACHE_TOKEN: 'separate-token' }, noRequest), /separate cache connection/);
  await assert.rejects(checkCacheWriteDenied({ ...env, SCCACHE_WEBDAV_ENDPOINT: 'https://cache.depot.dev' }, noRequest), /separate cache connection/);
  for (const ACTIONS_RESULTS_URL of [
    'https://results-receiver.actions.githubusercontent.com/',
    'https://cache.depot.dev.example.test/',
    'http://localhost.example.test/',
    'http://name:password@localhost:8123/',
  ]) {
    await assert.rejects(checkCacheWriteDenied({ ...env, ACTIONS_RESULTS_URL }, noRequest), /recognized Depot cache proxy/);
  }
});
