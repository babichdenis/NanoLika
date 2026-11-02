// What a link or an attachment is, for the pane and the picture viewer: the kind by the
// relay's mime when it is known, by the file's own tail otherwise, and the name to save it
// under. Pure answers, checked without a DOM.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const here = fileURLToPath(new URL('.', import.meta.url))
let dir
let media

before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'nm-media-'))
  await build({
    entryPoints: [join(here, '..', 'src/client/media.ts')],
    outfile: join(dir, 'media.mjs'),
    format: 'esm',
    platform: 'node',
    target: 'node22',
    bundle: true,
    logLevel: 'silent',
  })
  media = await import(pathToFileURL(join(dir, 'media.mjs')).href)
})

after(async () => {
  if (dir) await rm(dir, { recursive: true, force: true })
})

const link = (name) => `https://cloud.example.test/v1/files/files/1/${name}?t=abc`

test('a picture is a picture by mime or by its tail', () => {
  assert.equal(media.previewKindOf(link('a.png')), 'image')
  assert.equal(media.previewKindOf(link('a.JPG?x=1')), 'image')
  assert.equal(media.previewKindOf('https://cloud.example.test/v1/files/files/1/blob', 'image/webp'), 'image')
})

test('video, sound and PDF are told apart, and a page stays a page', () => {
  assert.equal(media.previewKindOf(link('clip.mp4')), 'video')
  assert.equal(media.previewKindOf(link('clip.mov')), 'video')
  assert.equal(media.previewKindOf(link('voice.ogg')), 'audio')
  assert.equal(media.previewKindOf(link('song.mp3')), 'audio')
  assert.equal(media.previewKindOf(link('letter.pdf')), 'pdf')
  assert.equal(media.previewKindOf('https://example.com/news'), 'url')
  assert.equal(media.previewKindOf(link('table.xlsx')), 'url')
})

test('the mime wins over a tail that says nothing', () => {
  assert.equal(media.previewKindOf(link('download'), 'video/mp4'), 'video')
  assert.equal(media.previewKindOf(link('download'), 'application/pdf'), 'pdf')
})

test('the name to save under comes from the URL, query and all removed', () => {
  assert.equal(media.nameOfUrl(link('lika-picture.jpg')), 'lika-picture.jpg')
  assert.equal(media.nameOfUrl('https://cloud.example.test/v1/files/files/1/%D0%BF%D0%B8%D1%81%D1%8C%D0%BC%D0%BE.pdf'), 'письмо.pdf')
  assert.equal(media.nameOfUrl('https://example.com/'), 'file')
})
