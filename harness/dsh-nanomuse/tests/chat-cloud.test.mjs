// The cloud on a chat: which of the four a row is, what its menu item does there, and what the
// person reads when the host refuses one. The row draws it, but the answers are a pure function,
// so they are checked here without a DOM.
import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL, fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const here = fileURLToPath(new URL('.', import.meta.url))
let dir
let cloud

before(async () => {
  dir = await mkdtemp(join(tmpdir(), 'nm-chat-cloud-'))
  await build({
    entryPoints: [join(here, '..', 'src/client/chat-cloud.ts')],
    outfile: join(dir, 'chat-cloud.mjs'),
    format: 'esm',
    platform: 'node',
    target: 'node22',
    bundle: true,
    logLevel: 'silent',
  })
  cloud = await import(pathToFileURL(join(dir, 'chat-cloud.mjs')).href)
})

after(async () => {
  if (dir) await rm(dir, { recursive: true, force: true })
})

/** The host's view as the column reads it: `sessions` holds a conversation (a cid), `synced` is the person's own mark. */
const view = (sessions = [], synced = []) => ({ sessions: new Set(sessions), synced: new Set(synced) })
/** A translator that hands back the key it was asked for, holes and all. */
const t = (key, values) => (values ? `${key}(${JSON.stringify(values)})` : key)

test('the main chat is in the cloud, whatever else is known about it', () => {
  assert.equal(cloud.chatCloud(view(), 'a', true), 'main')
  assert.equal(cloud.chatCloud(view(['a'], ['a']), 'a', true), 'main')
})

test('a side chat is where the host marks it: device, from the cloud, or local with no conversation', () => {
  // the person's own mark: in the cloud, and off is allowed again
  assert.equal(cloud.chatCloud(view(['a'], ['a']), 'a', false), 'device')
  // a conversation in the account with no mark here: another device or a messenger made it
  assert.equal(cloud.chatCloud(view(['a']), 'a', false), 'sync')
  // no cid at all: only this computer has the chat
  assert.equal(cloud.chatCloud(view(), 'a', false), 'local')
})

test('the menu item of each: cloud on, cloud off, delete whole, or nothing for the main chat', () => {
  assert.deepEqual(cloud.cloudItem(t, 'local'), { action: 'on', label: 'chSync' })
  assert.deepEqual(cloud.cloudItem(t, 'device'), { action: 'off', label: 'chUnsync' })
  // a chat from the cloud: the one thing the host allows is deleting it from the account whole
  assert.deepEqual(cloud.cloudItem(t, 'sync'), { action: 'off', label: 'chUnsync' })
  // the main chat: the item stays, inert, and says why
  assert.deepEqual(cloud.cloudItem(t, 'main'), { action: 'none', label: 'chUnsync', hint: 'chCloudMain' })
})

test('the badge says only where the copy lives', () => {
  assert.equal(cloud.cloudSaid(t, 'local'), 'chCloudOff')
  assert.equal(cloud.cloudSaid(t, 'device'), 'chCloudOn')
  assert.equal(cloud.cloudSaid(t, 'sync'), 'chCloudOn')
  assert.equal(cloud.cloudSaid(t, 'main'), 'chCloudOn')
})

test('a known refusal is said in our words, an unknown one in the host words', () => {
  assert.equal(cloud.cloudRefusal(t, 'main'), 'chCloudMain')
  assert.equal(cloud.cloudRefusal(t, 'synced-from-cloud'), 'chCloudRemote')
  assert.equal(cloud.cloudRefusal(t, 'not-synced-account'), 'chCloudNoSync')
  assert.equal(cloud.cloudRefusal(t, 'unknown-session'), 'chCloudFailed')
  assert.equal(cloud.cloudRefusal(t, 'something_new'), 'failed({"message":"something_new"})')
  assert.equal(cloud.cloudRefusal(t, ''), 'chCloudFailed')
  assert.equal(cloud.cloudRefusal(t, undefined), 'chCloudFailed')
})
