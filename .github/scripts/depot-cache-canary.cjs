const { createHash } = require('node:crypto');

async function checkCacheWriteDenied(env, request) {
  if (!env.RUNNER_NAME?.startsWith('depot-') || env.ACTIONS_CACHE_MODE !== 'read') {
    throw new Error('The cache probe needs a Depot runner with effective cache-mode: read');
  }
  if (env.DEPOT_CACHE_TOKEN || env.SCCACHE_WEBDAV_ENDPOINT) {
    throw new Error('Depot injected a separate cache connection into the read-only job');
  }
  if (!env.ACTIONS_CACHE_SERVICE_V2 || !env.ACTIONS_RESULTS_URL || !env.ACTIONS_RUNTIME_TOKEN) {
    throw new Error('The runner did not expose its Actions Cache v2 connection');
  }
  if (!/^\d+$/.test(env.GITHUB_RUN_ID) || !/^\d+$/.test(env.GITHUB_RUN_ATTEMPT)) {
    throw new Error('Missing canary run identity');
  }

  // Match actions/cache v2, which resolves its Twirp path from the runtime endpoint's origin.
  const path = '/twirp/github.actions.results.api.v1.CacheService/CreateCacheEntry';
  const url = URL.parse(path, env.ACTIONS_RESULTS_URL);
  if (!url) {
    throw new Error('The runner exposed an invalid Actions Cache v2 endpoint');
  }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if ((url.protocol !== 'https:' && !(loopback && url.protocol === 'http:')) ||
      url.username || url.password) {
    throw new Error('The Actions Cache v2 endpoint must use HTTPS or runner loopback HTTP and have no embedded credentials');
  }

  // A reservation tests the server-side permission without uploading any data.
  const key = `skippy-depot-cache-canary-${env.GITHUB_RUN_ID}-${env.GITHUB_RUN_ATTEMPT}-write-probe`;
  const version = createHash('sha256').update(key).digest('hex');
  const response = await request(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.ACTIONS_RUNTIME_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ key, version }),
    redirect: 'error',
    signal: AbortSignal.timeout(15000),
  });
  const result = await response.json().catch(() => null);
  if (response.ok && result?.ok === true) {
    throw new Error('Depot accepted a write reservation from a read-only job');
  }
  if ((response.status === 403 && result?.code === 'permission_denied') ||
      ((response.status === 403 || (response.ok && result?.ok === false)) &&
        typeof result?.message === 'string' && result.message.startsWith('cache write denied:'))) {
    return response.status;
  }
  throw new Error(`Depot did not return a recognized cache write denial (HTTP ${response.status})`);
}

module.exports = { checkCacheWriteDenied };
