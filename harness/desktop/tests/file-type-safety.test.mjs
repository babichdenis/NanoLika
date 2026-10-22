import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const cwd = fileURLToPath(new URL('..', import.meta.url));
test('malformed ASF completes instead of stalling the desktop event loop', () => {
  const code = `const ft=require('file-type'); const b=Buffer.alloc(55); Buffer.from('3026b2758e66cf11a6d9','hex').copy(b); ft.fromBuffer(b).then(()=>process.stdout.write('done'));`;
  const result = spawnSync(process.execPath, ['-e', code], { cwd, timeout: 5000, encoding: 'utf8' });
  assert.equal(result.error, undefined);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'done');
});
test('image recognition still accepts a normal PNG', () => {
  const code = `const ft=require('file-type');ft.fromBuffer(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==','base64')).then(x=>process.stdout.write(x.ext));`;
  const result = spawnSync(process.execPath, ['-e', code], { cwd, timeout: 5000, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stdout, 'png');
});
