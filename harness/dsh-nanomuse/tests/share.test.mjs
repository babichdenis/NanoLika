import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtemp, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {localFileLinks, presignGet} from '../lib/share.js'

test('local file links are found, spaces and angle brackets included', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'nanolika-share-'))
  const one = join(dir, 'clip one.mp4')
  await writeFile(one, 'x')
  const two = join(dir, 'take.md')
  await writeFile(two, 'y')
  const text = `A [clip](<${one}>) and [notes](${two}) and [remote](https://example.com/a)`
  assert.deepEqual(localFileLinks(text).map((x) => x.path), [one, two])
})

test('a presigned link names the object and carries a signature, never the secret', () => {
  const cfg = {
    endpoint: 'https://s3.regru.cloud',
    region: 'us-east-1',
    bucket: 'nanolika',
    accessKeyId: 'AKIAEXAMPLE',
    secretAccessKey: 'secret-not-in-url',
  }
  const url = presignGet(cfg, 'files/abc/clip one.mp4', 'clip one.mp4')
  assert.match(url, /^https:\/\/s3\.regru\.cloud\/nanolika\/files\/abc\/clip%20one\.mp4\?/)
  assert.match(url, /X-Amz-Signature=[0-9a-f]{64}/)
  assert.match(url, /response-content-disposition=attachment/)
  assert.ok(!url.includes(cfg.secretAccessKey))
})
