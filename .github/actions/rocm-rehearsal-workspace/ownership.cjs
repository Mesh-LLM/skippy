const { appendFileSync, statSync } = require('node:fs');
const { resolve } = require('node:path');
const { spawnSync } = require('node:child_process');

// Actions mounts the native runner's _work at /__w. The compiler container
// runs as root for AMD device access, but JY must later remove _work as its
// standard account. Register before package actions so this post runs last.
const work = resolve(process.env.GITHUB_WORKSPACE, '../..');
if (work !== '/__w' || process.getuid() !== 0) {
  throw new Error('ROCm workspace ownership repair requires the root job container at /__w');
}
if (!process.env.STATE_owner) {
  const { uid, gid } = statSync(work);
  if (uid === 0) throw new Error('Cannot identify the native JY workspace owner');
  appendFileSync(process.env.GITHUB_STATE, `owner=${uid}:${gid}\n`);
} else {
  const owner = process.env.STATE_owner;
  if (!/^[1-9][0-9]*:[0-9]+$/.test(owner)) throw new Error('Invalid native workspace owner');
  const result = spawnSync('chown', ['-R', '--no-dereference', '--from=0', owner, work], {
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error('ROCm workspace ownership cleanup failed');
  console.log('Restored root-created ROCm workspace files to the native JY account');
}
