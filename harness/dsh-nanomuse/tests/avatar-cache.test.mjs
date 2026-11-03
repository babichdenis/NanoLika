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
      for (const ext of mood === 'error' ? ['webp'] : ['webp', 'webm']) {
        await writeFile(join(dir, `lika-${mood}.${ext}`), `${mood}.${ext}`)
      }
      if (mood !== 'error') await writeFile(join(dir, `lika-${mood}-small.webm`), `${mood}-small.webm`)
    }
    const folder = pathToFileURL(`${dir}/`)
    const old = avatarRevision(folder)
    assert.equal(avatarRevision(folder), old)
    await writeFile(join(dir, 'lika-idle.webm'), 'replacement Lika animation')
    assert.notEqual(avatarRevision(folder), old)
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('a versioned bundled avatar is immutable, an unversioned one revalidates', async () => {
  const server = createServer((req, res) => { void serveAsset(req, res) })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  try {
    const base = `http://127.0.0.1:${server.address().port}/nanomuse/assets/lika-idle.webp`
    const versioned = await fetch(`${base}?v=test`)
    assert.equal(versioned.status, 200)
    assert.equal(versioned.headers.get('cache-control'), 'public, max-age=31536000, immutable')
    assert.equal(versioned.headers.get('content-type'), 'image/webp')
    assert.ok((await versioned.arrayBuffer()).byteLength > 0)
    const plain = await fetch(base)
    assert.equal(plain.status, 200)
    assert.equal(plain.headers.get('cache-control'), 'no-cache')
  } finally { await new Promise((resolve) => server.close(resolve)) }
})
