import test from 'node:test';
import assert from 'node:assert/strict';
import {modelCards, filterCounts, filterCards, vendorRows} from '../model-sheet.js';

const data = {data: [
  {id: 'gpt-4.1', name: 'GPT-4.1', nanomuse: {kind: 'chat', vendor: '~Openai', for: ['chat']}},
  {id: 'claude-sonnet', name: 'Claude Sonnet', nanomuse: {kind: 'chat', vendor: 'Anthropic', for: ['chat']}},
  {id: 'qwen-coder', name: 'Qwen3 Coder', nanomuse: {kind: 'chat', vendor: 'Alibaba', for: ['chat']}},
  {id: 'flux', name: 'FLUX', nanomuse: {kind: 'image', vendor: 'Black Forest Labs'}},
  {id: 'kling', name: 'Kling', nanomuse: {kind: 'video', vendor: 'Kuaishou'}},
  {id: 'tts', name: 'TTS', nanomuse: {kind: 'audio', audio_kind: 'speech', vendor: 'OpenAI'}},
  {id: 'whisper', name: 'Whisper', nanomuse: {kind: 'audio', audio_kind: 'transcribe', vendor: 'OpenAI'}},
]};

test('the web picker filters mirror the cabinet and count what they show', () => {
  const cards = modelCards(data);
  const counts = Object.fromEntries(filterCounts(cards, {
    favourites: new Set(['gpt-4.1', 'flux']),
    selected: new Set(['gpt-4.1', 'claude-sonnet', 'flux']),
  }).map(([key, , count]) => [key, count]));
  assert.deepEqual(counts, {
    __fav: 2, __mine: 3, __code: 2, chat: 3, image: 1, video: 1, speech: 1, transcribe: 1,
  });
  assert.deepEqual(filterCards(cards, {kind: 'speech'}).map((card) => card.id), ['tts']);
  assert.deepEqual(filterCards(cards, {kind: '__code'}).map((card) => card.id), ['claude-sonnet', 'qwen-coder']);
  assert.deepEqual(filterCards(cards, {kind: '__mine', selected: new Set(['flux'])}).map((card) => card.id), ['flux']);
});

test('vendor names are normalized and small makers become Other', () => {
  const cards = modelCards(data);
  const rows = vendorRows(cards);
  assert.deepEqual(rows, [
    {name: 'OpenAI', count: 3},
    {name: 'Anthropic', count: 1},
    {name: 'Qwen', count: 1},
    {name: 'Другие', count: 2},
  ]);
  assert.deepEqual(filterCards(cards, {vendor: 'Другие'}).map((card) => card.id), ['flux', 'kling']);
});
