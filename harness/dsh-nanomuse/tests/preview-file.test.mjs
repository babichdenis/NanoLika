// The side pane plays a file the agent made: kindOfFile picks the player, rangeOf
// answers a video's "give me the next chunk" without reading the whole clip.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, writeFile } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import { kindOfFile } from '../lib/preview-kind.js'
import { rangeOf, resolveUserPath, servePreviewFile } from '../lib/preview-file.js'

test('kindOfFile picks a player for what the pane can show', () => {
  assert.equal(kindOfFile('/tmp/clip.mp4'), 'video')
  assert.equal(kindOfFile('/tmp/clip.MOV'), 'video')
  assert.equal(kindOfFile('/tmp/voice.ogg'), 'audio')
  assert.equal(kindOfFile('/tmp/face.webp'), 'image')
  assert.equal(kindOfFile('/tmp/letter.pdf'), 'pdf')
  assert.equal(kindOfFile('/tmp/notes.md'), 'text')
  assert.equal(kindOfFile('/tmp/report.docx'), undefined)
  assert.equal(kindOfFile('/tmp/archive.zip'), undefined)
})

test('resolveUserPath expands the home shorthand and keeps absolute paths', () => {
  assert.equal(resolveUserPath('~/outputs/clip.mp4'), join(homedir(), 'outputs/clip.mp4'))
  assert.equal(resolveUserPath('/tmp/clip.mp4'), '/tmp/clip.mp4')
})

test('rangeOf serves a slice, an open end and a suffix; refuses the rest', () => {
  assert.equal(rangeOf(undefined, 100), undefined)
  assert.deepEqual(rangeOf('bytes=0-99', 100), { start: 0, end: 99 })
  assert.deepEqual(rangeOf('bytes=50-', 100), { start: 50, end: 99 })
  assert.deepEqual(rangeOf('bytes=50-500', 100), { start: 50, end: 99 })
  assert.deepEqual(rangeOf('bytes=-10', 100), { start: 90, end: 99 })
  assert.equal(rangeOf('bytes=200-300', 100), 'invalid')
  assert.equal(rangeOf('bytes=9-2', 100), 'invalid')
  assert.equal(rangeOf('bytes=0-1,5-6', 100), 'invalid')
  assert.equal(rangeOf('bytes=-0', 100), 'invalid')
  assert.equal(rangeOf('bytes=abc', 100), 'invalid')
})

test('servePreviewFile returns the clip whole, a byte slice, and a 404 for a stranger', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'preview-file-'))
  const file = join(dir, 'clip.mp4')
  await writeFile(file, '0123456789')
  const server = createServer((req, res) => void servePreviewFile(req, res))
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  const at = (path, init) => fetch(`http://127.0.0.1:${port}/nanomuse/file?path=${encodeURIComponent(path)}`, init)
  try {
    const whole = await at(file)
    assert.equal(whole.status, 200)
    assert.equal(whole.headers.get('content-type'), 'video/mp4')
    assert.equal(whole.headers.get('accept-ranges'), 'bytes')
    assert.equal(await whole.text(), '0123456789')
    const slice = await at(file, { headers: { Range: 'bytes=2-4' } })
    assert.equal(slice.status, 206)
    assert.equal(slice.headers.get('content-range'), 'bytes 2-4/10')
    assert.equal(await slice.text(), '234')
    assert.equal((await at(join(dir, 'gone.mp4'))).status, 404)
    assert.equal((await at(join(dir, 'notes.docx'))).status, 404)
  } finally {
    await new Promise((resolve) => server.close(resolve))
  }
})
