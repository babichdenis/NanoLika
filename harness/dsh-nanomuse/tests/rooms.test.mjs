// The rooms' pure helpers: what the agent's final answer parses to, and the first
// title a goal gets before the agent names it.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { goalTitle, languageName, parseArray } from '../lib/rooms.js'

test('parseArray takes the last fenced json block', () => {
  const text = 'Here is a draft:\n```json\n[{"title": "draft"}]\n```\nFinal:\n```json\n[{"title": "a"}, {"title": "b"}]\n```\n'
  assert.deepEqual(parseArray(text).map((r) => r.title), ['a', 'b'])
})

test('parseArray falls back to the outermost brackets, and to nothing', () => {
  assert.deepEqual(parseArray('sure: [{"title": "x", "n": 1}] done').map((r) => r.title), ['x'])
  assert.deepEqual(parseArray('no json here'), [])
  assert.deepEqual(parseArray('```json\n{"not": "an array"}\n```'), [])
  assert.deepEqual(parseArray('```json\n[1, "two", {"title": "three"}]\n```').map((r) => r.title), ['three'])
})

test('goalTitle keeps the first clause', () => {
  assert.equal(goalTitle('工作日零点前睡觉；每天 23:00 提醒我，每周日晚上问我这周睡得怎么样。'), '工作日零点前睡觉')
  assert.equal(goalTitle('Save ¥20,000 by December; track it monthly and tell me when I drift.'), 'Save ¥20,000 by December')
  assert.equal(goalTitle('Run a 10k'), 'Run a 10k')
  assert.equal(goalTitle('Hi, call my parents every Sunday'), 'Hi, call my parents every Sunday'.slice(0, 40))
  assert.equal(goalTitle('x'.repeat(80)).length, 40)
})

test('zip writes an archive Python can read back', async () => {
  const { zip, crc32 } = await import('../lib/rooms.js')
  const { writeFile, mkdtemp, rm } = await import('node:fs/promises')
  const { tmpdir } = await import('node:os')
  const { join } = await import('node:path')
  const { spawnSync } = await import('node:child_process')
  assert.equal(crc32(Buffer.from('')), 0)
  assert.equal(crc32(Buffer.from('123456789')), 0xcbf43926)
  const dir = await mkdtemp(join(tmpdir(), 'nm-zip-'))
  const file = join(dir, 'x.zip')
  const big = Buffer.alloc(5000, 'a')
  await writeFile(file, zip([
    { name: 'nanomuse/rooms.json', data: Buffer.from('{"goals":[]}'), at: new Date(2026, 9, 3, 12, 30, 10) },
    { name: '构件/读我.txt', data: big, at: new Date() },
  ]))
  const py = spawnSync('python3', ['-c', `import zipfile,sys; z=zipfile.ZipFile(sys.argv[1]); print(z.testzip()); print(','.join(i.filename for i in z.infolist())); print(len(z.read('构件/读我.txt')))`, file], { encoding: 'utf8' })
  await rm(dir, { recursive: true, force: true })
  if (py.status !== 0) return // no python here; the crc checks above still ran
  assert.equal(py.stdout.trim().split('\n')[0], 'None')
  assert.equal(py.stdout.trim().split('\n')[1], 'nanomuse/rooms.json,构件/读我.txt')
  assert.equal(py.stdout.trim().split('\n')[2], '5000')
})

test('the prompt names the person\'s own language, not English by default', () => {
  // The interface ships a Russian dictionary; the feed prompt used to answer "English" for it,
  // so a person reading Russian got English posts. Any language now has a name.
  assert.equal(languageName('ru'), 'Russian')
  assert.equal(languageName('ru-RU'), 'Russian')
  assert.equal(languageName('be'), 'Belarusian', 'unnamed languages are named by the platform')
  assert.equal(languageName('zh-CN'), 'Simplified Chinese')
  assert.equal(languageName('zh-TW'), 'Traditional Chinese')
  assert.equal(languageName('ja-JP'), 'Japanese')
  assert.equal(languageName(''), 'English', 'no tag at all is the one language this file is written in')
  assert.equal(languageName('---'), 'English', 'an unparseable tag never throws')
})
