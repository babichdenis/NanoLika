import assert from 'node:assert/strict'
import test from 'node:test'
import { spoken } from '../lib/voice.js'

test('markup is not read aloud', () => {
  assert.equal(spoken('**Привет**, [ссылка](http://x) и `код`!', 600), 'Привет, ссылка и код!')
})

test('an answer is cut at a sentence, not mid-word', () => {
  assert.equal(spoken('Один. Два. Три.', 8), 'Один.')
})

test('a short answer is read whole', () => {
  assert.equal(spoken('Готово.', 600), 'Готово.')
})
