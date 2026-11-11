import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile, readdir, rename } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { StateStore, atomicReplace } from '../lib/state-store.js'

test('overlapping saves are ordered snapshots and a failed save does not block later saves', async () => {
  const writes = []
  let release
  const gate = new Promise(resolve => { release = resolve })
  const store = new StateStore(async (_path, text) => {
    const obj = JSON.parse(text)
    if (obj.number === 1) await gate
    if (obj.number === 2) throw new Error('disk failed')
    writes.push(obj)
  })
  const value = { number: 1, pending: { mid: 'stable' } }
  const first = store.write('unused', value)
  value.number = 2
  const second = store.write('unused', value)
  value.number = 3
  const third = store.write('unused', value)
  release()
  await first
  await assert.rejects(second, /disk failed/)
  await third
  assert.deepEqual(writes, [{ number: 1, pending: { mid: 'stable' } }, { number: 3, pending: { mid: 'stable' } }])
})

test('failure before rename preserves the existing snapshot', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'nanolika-state-'))
  try {
    const path = join(dir, 'cloud.json')
    await writeFile(path, '{"old":true}\n')
    await assert.rejects(atomicReplace(path, '{"new":true}', async () => { throw new Error('power loss before rename') }))
    assert.equal(await readFile(path, 'utf8'), '{"old":true}\n')
    assert.deepEqual((await readdir(dir)).sort(), ['cloud.json'])
    await atomicReplace(path, '{"new":true}\n')
    assert.equal(await readFile(path, 'utf8'), '{"new":true}\n')
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('a failure after replacement is reported as an uncertain write', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'nanolika-state-'))
  try {
    const path = join(dir, 'cloud.json')
    await writeFile(path, '{"previous":true}\n')
    const store = new StateStore(async (target, text) => {
      await atomicReplace(target, text, rename, async () => { throw new Error('directory fsync failed after rename') })
    })
    await assert.rejects(store.write(path, { pending: { mid: 'id' } }), /fsync failed/)
    assert.equal(JSON.parse(await readFile(path, 'utf8')).pending.mid, 'id')
  } finally { await rm(dir, { recursive: true, force: true }) }
})


test('failure to fsync the temporary file leaves the previous state intact', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'nanolika-state-'))
  try {
    const path = join(dir, 'cloud.json')
    await writeFile(path, '{"previous":true}\n')
    await assert.rejects(atomicReplace(path, '{"pending":true}', rename, async () => {}, async () => { throw new Error('file fsync failed') }), /file fsync failed/)
    assert.equal(await readFile(path, 'utf8'), '{"previous":true}\n')
    assert.deepEqual((await readdir(dir)).sort(), ['cloud.json'])
  } finally { await rm(dir, { recursive: true, force: true }) }
})
