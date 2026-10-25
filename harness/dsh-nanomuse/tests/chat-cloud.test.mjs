// The cloud on a chat: which of the four a row is, whether it offers a switch, and what the
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

const view = (sessions = [], synced = []) => ({ sessions: new Set(sessions), synced: new Set(synced) })
/** A translator that hands back the key it was asked for, holes and all. */
const t = (key, values) => (values ? `${key}(${JSON.stringify(values)})` : key)

test('the main chat is in the cloud, whatever else is known about it', () => {
  assert.equal(cloud.chatCloud(view(), 'a', true), 'main')
  assert.equal(cloud.chatCloud(view(['a'], ['a']), 'a', true), 'main')
})

test("a side chat is where this device's switch says, and elsewhere when the host holds it", () => {
  assert.equal(cloud.chatCloud(view(['a'], ['a']), 'a', false), 'synced')
  // a chat from another device or a messenger: the host has a conversation for it, this device never switched it
  assert.equal(cloud.chatCloud(view(['a']), 'a', false), 'remote')
  assert.equal(cloud.chatCloud(view(), 'a', false), 'local')
})

test('only the two states the person works from here take the switch', () => {
  assert.equal(cloud.switchable('synced'), true)
  assert.equal(cloud.switchable('local'), true)
  assert.equal(cloud.switchable('main'), false)
  assert.equal(cloud.switchable('remote'), false)
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
