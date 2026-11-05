import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { desktopOAuth } from '../lib/desktop-oauth.js'

function callback(attempt, extra = {}) {
  const start = new URL(attempt.url)
  const url = new URL(start.searchParams.get('desktop_callback'))
  url.searchParams.set('state', start.searchParams.get('desktop_state'))
  for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value)
  return url
}
for (const provider of ['google', 'yandex']) test(`${provider}: callback exchanges once with matching PKCE`, async () => {
  let calls = 0
  let attempt
  attempt = await desktopOAuth({ origin: 'https://cloud.test', provider, failed: (reason) => assert.fail(reason),
    exchange: async (code, verifier, signal) => {
      calls++
      assert.equal(code, 'c'.repeat(43))
      assert.equal(signal.aborted, false)
      assert.equal(createHash('sha256').update(verifier).digest('base64url'), new URL(attempt.url).searchParams.get('desktop_challenge'))
    },
  })
  try {
    for (const state of ['wrong', 'я'.repeat(43)]) {
      assert.equal((await fetch(callback(attempt, { state, code: 'c'.repeat(43) }))).status, 400)
    }
    assert.equal(calls, 0)
    const response = await fetch(callback(attempt, { code: 'c'.repeat(43) }))
    assert.equal(response.status, 200)
    assert.match(await response.text(), /Signed in/)
    assert.equal(response.headers.get('cache-control'), 'no-store')
    assert.equal(calls, 1)
  } finally { attempt.cancel() }
})
test('cancellation aborts an in-flight exchange; no successful callback follows', async () => {
  let began
  const started = new Promise((resolve) => { began = resolve })
  let aborted
  const cancelled = new Promise((resolve) => { aborted = resolve })
  const attempt = await desktopOAuth({ origin: 'https://cloud.test', provider: 'yandex', failed: () => {},
    exchange: async (_code, _verifier, signal) => {
      began()
      await new Promise((_resolve, reject) => signal.addEventListener('abort', () => { aborted(); reject(new Error('cancelled')) }, { once: true }))
    },
  })
  const response = fetch(callback(attempt, { code: 'c'.repeat(43) }))
  await started
  attempt.cancel()
  await cancelled
  assert.equal((await response).status, 400)
})
test('provider denial ends attempt; timeout closes unused listener', async () => {
  let denied
  const attempt = await desktopOAuth({ origin: 'https://cloud.test', provider: 'google', exchange: async () => assert.fail(), failed: (reason) => { denied = reason } })
  assert.equal((await fetch(callback(attempt, { error: 'denied' }))).status, 400)
  assert.equal(denied, 'denied')
  let timedOut
  const done = new Promise((resolve) => { timedOut = resolve })
  await desktopOAuth({ origin: 'https://cloud.test', provider: 'google', timeoutMs: 20, exchange: async () => assert.fail(), failed: timedOut })
  // Keep the test process alive while the listener's own timeout is unref'd.
  const timer = setTimeout(() => {}, 1000)
  assert.equal(await done, 'timeout')
  clearTimeout(timer)
})
