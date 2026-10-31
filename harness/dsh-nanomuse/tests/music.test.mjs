import assert from 'node:assert/strict'
import test from 'node:test'
import { Relay } from '../lib/relay.js'

test('generateMusic posts the chosen model and options to /v1/audio/music', async () => {
  let seen
  const relay = new Relay('https://relay.example.test', async (url, options) => {
    seen = { url, options }
    return new Response(new Uint8Array([0x49, 0x44, 0x33]), { status: 200, headers: { 'content-type': 'audio/mpeg' } })
  })
  const bytes = await relay.generateMusic('fixture-key', 'music-suno-track', 'calm piano', { style: 'lo-fi', instrumental: true })
  assert.equal(seen.url, 'https://relay.example.test/v1/audio/music')
  assert.equal(seen.options.method, 'POST')
  assert.equal(seen.options.headers.authorization, 'Bearer fixture-key')
  assert.deepEqual(JSON.parse(seen.options.body), { model: 'music-suno-track', prompt: 'calm piano', style: 'lo-fi', instrumental: true })
  assert.equal(bytes.toString(), 'ID3')
})
