import assert from 'node:assert/strict'
import test from 'node:test'
import { languageOf, spoken, voiceFor } from '../lib/voice.js'

test('markup is not read aloud', () => {
  assert.equal(spoken('**Привет**, [ссылка](http://x) и `код`!', 600), 'Привет, ссылка и код!')
})

test('an answer is cut at a sentence, not mid-word', () => {
  assert.equal(spoken('Один. Два. Три.', 8), 'Один.')
})

test('a short answer is read whole', () => {
  assert.equal(spoken('Готово.', 600), 'Готово.')
})

test('the voice follows the language of the words', () => {
  // a Russian answer read by the English default voice is the "wrong talk" people hear
  assert.equal(languageOf('Привет, готово.'), 'ru')
  assert.equal(languageOf('你好，完成了。'), 'zh')
  assert.equal(languageOf('Done.'), '')
  assert.equal(voiceFor('Привет', ''), 'Milena')
  assert.equal(voiceFor('你好', ''), 'Tingting')
  assert.equal(voiceFor('Done', ''), '')
  // a voice the person chose wins over any detection
  assert.equal(voiceFor('Привет', 'Albert'), 'Albert')
})
