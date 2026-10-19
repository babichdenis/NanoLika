import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createServer } from 'node:http'
import { avatarRevision } from '../../../scripts/avatar-revision.mjs'
import { serveAsset } from '../lib/index.js'

test('a same-version artwork replacement changes the browser cache key', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'lika-artwork-'))
  try {
    for (const mood of ['idle', 'working', 'waiting', 'happy', 'error']) {
      for (const ext of mood === 'error' ? ['webp'] : ['webp', 'mp4']) {
        await writeFile(join(dir, `dragon-${mood}.${ext}`), `${mood}.${ext}`)
      }
    }
    const folder = pathToFileURL(`${dir}/`)
    const old = avatarRevision(folder)
    assert.equal(avatarRevision(folder), old)
    await writeFile(join(dir, 'dragon-idle.mp4'), 'replacement Lika animation')
    assert.notEqual(avatarRevision(folder), old)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('bundled avatars are revalidated instead of retaining a day-old face', async () => {
  const server = createServer((req, res) => { void serveAsset(req, res) })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  try {
    const url = `http://127.0.0.1:${server.address().port}/nanomuse/assets/dragon-idle.webp?v=test`
    const res = await fetch(url)
    assert.equal(res.status, 200)
    assert.equal(res.headers.get('cache-control'), 'no-cache')
    assert.equal(res.headers.get('content-type'), 'image/webp')
    assert.ok((await res.arrayBuffer()).byteLength > 0)
  } finally { await new Promise((resolve) => server.close(resolve)) }
})
