/**
 * The Whisper provider's own halves: which language Whisper is told, how a recording is
 * read out of its WAV container, and what preparation does to its state — with the
 * network and the native library stood in for, so that none of this needs a download or a
 * `.node` addon. The live check of the real model is a separate one-off script; these are
 * the pieces that must not break.
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { LANGUAGES, createSpeechWhisper, orderSources, parseWave, registerSpeechWhisper, whisperLanguage } from '../lib/speech-whisper.js'

/** A 16 kHz mono PCM16 WAV, with an optional alien chunk before `fmt `. */
function wave({ sampleRate = 16000, channels = 1, bits = 16, frames = 1600, alien = false } = {}) {
  const data = Buffer.alloc(frames * 2 * channels)
  for (let index = 0; index < frames; index += 1) data.writeInt16LE(Math.round(Math.sin(index / 8) * 8000), index * 2 * channels)
  const fmt = Buffer.alloc(16)
  fmt.writeUInt16LE(1, 0)
  fmt.writeUInt16LE(channels, 2)
  fmt.writeUInt32LE(sampleRate, 4)
  fmt.writeUInt32LE(sampleRate * channels * (bits / 8), 8)
  fmt.writeUInt16LE(channels * (bits / 8), 12)
  fmt.writeUInt16LE(bits, 14)
  const chunks = []
  if (alien) {
    // What `ffmpeg` writes: a LIST chunk between the header and the format.
    const list = Buffer.from('INFOISFTnano', 'ascii')
    chunks.push(Buffer.concat([Buffer.from('LIST', 'ascii'), Buffer.from([list.length & 0xff, 0, 0, 0]), list]))
  }
  chunks.push(Buffer.concat([Buffer.from('fmt ', 'ascii'), Buffer.from([16, 0, 0, 0]), fmt]))
  chunks.push(Buffer.concat([Buffer.from('data', 'ascii'), Buffer.from([data.length & 0xff, (data.length >> 8) & 0xff, (data.length >> 16) & 0xff, (data.length >> 24) & 0xff]), data]))
  const body = Buffer.concat(chunks)
  const header = Buffer.alloc(12)
  header.write('RIFF', 0, 'ascii')
  header.writeUInt32LE(4 + body.length, 4)
  header.write('WAVE', 8, 'ascii')
  return new Uint8Array(Buffer.concat([header, body]))
}

/** A WAV as the buffers a fake download hands out: three pieces, so progress has somewhere to happen. */
function pieces(bytes) {
  const third = Math.ceil(bytes.length / 3)
  return [bytes.subarray(0, third), bytes.subarray(third, third * 2), bytes.subarray(third * 2)]
}

const digest = (bytes) => createHash('sha256').update(bytes).digest('hex')

/** Three pinned files and a detector, small enough to be held in a test. */
function release() {
  const files = {
    'tiny-encoder.int8.onnx': Buffer.from('encoder-graph-for-a-test-'.repeat(4)),
    'tiny-decoder.int8.onnx': Buffer.from('decoder-graph-for-a-test-'.repeat(8)),
    'tiny-tokens.txt': Buffer.from('token\n'.repeat(16)),
    'silero_vad.onnx': Buffer.from('detector\n'.repeat(4)),
  }
  const asset = (name) => ({ name, path: `repo/resolve/abc/${name}`, bytes: files[name].length, sha256: digest(files[name]) })
  return {
    files,
    assets: [asset('tiny-encoder.int8.onnx'), asset('tiny-decoder.int8.onnx'), asset('tiny-tokens.txt')],
    vadAsset: asset('silero_vad.onnx'),
  }
}

/** A network that serves the release, optionally corrupting one file. */
function fakeFetch(files, { corrupt = null, onGet = null } = {}) {
  const calls = []
  const fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method ?? 'GET' })
    const name = decodeURIComponent(new URL(url).pathname.split('/').pop())
    const body = files[name]
    if (init.method === 'HEAD') return new Response(null, { status: body === undefined ? 404 : 200 })
    if (body === undefined) return new Response('not found', { status: 404 })
    if (onGet !== null) await onGet(name, init)
    const bytes = corrupt === name ? Buffer.concat([body.subarray(0, body.length - 1), Buffer.from('!')]) : body
    return new Response(new ReadableStream({ start(controller) { for (const piece of pieces(bytes)) controller.enqueue(new Uint8Array(piece)); controller.close() } }), {
      status: 200,
      headers: { 'content-length': String(bytes.length) },
    })
  }
  return { fetch, calls }
}

/** A recognizer that answers a script instead of running a model. */
function fakeLoader(script = () => 'Привет, это проверка') {
  const seen = { warmed: [], languages: [], samples: [] }
  const load = async (paths, config) => {
    assert.equal(paths.encoder.endsWith('tiny-encoder.int8.onnx'), true)
    assert.equal(typeof config.threads, 'number')
    return {
      async warm(language) {
        seen.warmed.push(language)
      },
      async recognize(samples, language) {
        seen.languages.push(language)
        seen.samples.push(samples)
        return script(samples, language)
      },
    }
  }
  return { load, seen }
}

/** Resolve once the provider's preparation has settled. */
function settled(provider, timeoutMs = 3000) {
  return new Promise((resolve, reject) => {
    const started = Date.now()
    const check = () => {
      const phase = provider.snapshot().phase
      if (phase === 'ready' || phase === 'failed' || phase === 'cancelled') return resolve(provider.snapshot())
      if (Date.now() - started > timeoutMs) return reject(new Error(`preparation stuck in ${phase}`))
      setTimeout(check, 2)
    }
    check()
  })
}

async function scratch() {
  return await mkdtemp(join(tmpdir(), 'whisper-test-'))
}

/** Every state a subscription saw, in order. */
function phases(provider) {
  const seen = []
  provider.subscribe(() => seen.push(provider.snapshot()))
  return seen
}

/** The phases those states went through, with the repeats of a long download folded. */
function transitions(seen) {
  return seen.map((state) => state.phase).filter((phase, index, all) => all[index - 1] !== phase)
}

test('Russian is advertised first, and an empty hint means automatic detection', () => {
  assert.equal(LANGUAGES[0], 'ru')
  assert.deepEqual([...LANGUAGES], ['ru', 'en', 'auto'])
  assert.equal(whisperLanguage('ru'), 'ru')
  assert.equal(whisperLanguage('ru-RU'), 'ru')
  assert.equal(whisperLanguage('ru_RU'), 'ru')
  assert.equal(whisperLanguage('EN'), 'en')
  assert.equal(whisperLanguage('auto'), '')
  assert.equal(whisperLanguage(''), 'ru', 'no hint falls back to the configured language')
  assert.equal(whisperLanguage('', ''), '', 'an empty fallback asks Whisper to decide')
  assert.equal(whisperLanguage('   ', 'ru'), 'ru')
  assert.equal(whisperLanguage('de'), 'de', 'a language Whisper knows is passed on, advertised or not')
  assert.equal(whisperLanguage('кириллица'), '', 'what is not a code is a request for detection')
})

test('a recording is read out of its container, whatever chunks a writer left around it', () => {
  const plain = parseWave(wave({ frames: 1600 }), 1024 * 1024)
  assert.equal(plain.sampleRate, 16000)
  assert.equal(plain.seconds, 0.1)
  assert.equal(plain.samples.length, 1600)
  const expected = Math.round(Math.sin(2 / 8) * 8000) / 0x8000
  assert.ok(Math.abs(plain.samples[2] - expected) < 0.001, 'the sample comes back as a fraction of full scale')

  const withList = parseWave(wave({ frames: 3200, alien: true }), 1024 * 1024)
  assert.equal(withList.samples.length, 3200)
  assert.equal(withList.seconds, 0.2)
})

test('a recording the recognizer cannot read is refused with a reason', () => {
  assert.throws(() => parseWave(new Uint8Array(10), 1024), /not a WAV file/)
  assert.throws(() => parseWave(Buffer.alloc(64), 1024), /not a WAV file/)
  assert.throws(() => parseWave(wave({ channels: 2 }), 1024 * 1024), /mono/)
  assert.throws(() => parseWave(wave({ sampleRate: 44100 }), 1024 * 1024), /16000 Hz/)
  assert.throws(() => parseWave(wave({ bits: 8 }), 1024 * 1024), /16-bit/)
  assert.throws(() => parseWave(wave({ frames: 10 }), 44), /larger than/)
})

test('a preparation downloads the three files, verifies them, then loads', async () => {
  const root = await scratch()
  const { files, assets, vadAsset } = release()
  const { fetch } = fakeFetch(files)
  const { load, seen } = fakeLoader()
  let clock = 0
  const provider = createSpeechWhisper(
    { dataRoot: root, modelOrigins: ['https://one.test'], progressIntervalMs: 1 },
    { assets, vadAsset, fetch, load, now: () => (clock += 50) },
  )
  assert.equal(provider.info.id, 'whisper-local')
  assert.equal(provider.info.location, 'host-local')
  assert.deepEqual([...provider.info.languages], ['ru', 'en', 'auto'])
  assert.deepEqual(provider.info.downloadSources, ['https://one.test'])
  assert.equal(provider.snapshot().phase, 'unprepared')

  const seen_phases = phases(provider)
  provider.preparation.prepare()
  const state = await settled(provider)

  assert.equal(state.phase, 'ready')
  assert.deepEqual(transitions(seen_phases), ['checking', 'downloading', 'loading', 'ready'])
  const progress = seen_phases.filter((state) => state.phase === 'downloading' && state.step === 'model')
  assert.ok(progress.some((state) => state.completedBytes > 0), 'bytes are reported as they arrive')
  assert.equal(progress[0].completedBytes, 0)
  assert.equal(progress.find((state) => state.completedBytes > 0).totalBytes, files['tiny-encoder.int8.onnx'].length)
  assert.ok(seen_phases.some((state) => state.step === 'verify'), 'the hash is checked before the file is published')
  for (const name of Object.keys(files)) assert.equal(existsSync(join(root, name)), true, `${name} is on disk`)
  assert.deepEqual(seen.warmed, ['ru'], 'the default language is warmed on load')
  assert.equal(existsSync(join(root, 'tiny-encoder.int8.onnx.part')), false)
  await rm(root, { recursive: true, force: true })
})

test('a download source the provider does not offer is refused rather than tried', async () => {
  const root = await scratch()
  const { files, assets, vadAsset } = release()
  const provider = createSpeechWhisper({ dataRoot: root, modelOrigins: ['https://one.test'] }, { assets, vadAsset, fetch: fakeFetch(files).fetch, load: fakeLoader().load })
  provider.preparation.prepare({ downloadSource: 'https://elsewhere.test' })
  const state = await settled(provider)
  assert.equal(state.phase, 'failed')
  assert.match(state.message, /not a download source/)
  await rm(root, { recursive: true, force: true })
})

test('a file whose bytes do not match the pinned hash fails preparation and leaves nothing behind', async () => {
  const root = await scratch()
  const { files, assets, vadAsset } = release()
  const { fetch } = fakeFetch(files, { corrupt: 'tiny-decoder.int8.onnx' })
  const provider = createSpeechWhisper({ dataRoot: root, modelOrigins: ['https://one.test'], progressIntervalMs: 1 }, { assets, vadAsset, fetch, load: fakeLoader().load })
  provider.preparation.prepare()
  const state = await settled(provider)
  assert.equal(state.phase, 'failed')
  assert.equal(state.download?.reason, 'integrity')
  assert.equal(state.download?.resource, 'tiny-decoder.int8.onnx')
  assert.equal(existsSync(join(root, 'tiny-decoder.int8.onnx')), false)
  assert.equal(existsSync(join(root, 'tiny-decoder.int8.onnx.part')), false)
  await rm(root, { recursive: true, force: true })
})

test('a cancelled preparation stops downloading and says so', async () => {
  const root = await scratch()
  const { files, assets, vadAsset } = release()
  const stalled = (_url, init = {}) =>
    new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true })
    })
  const provider = createSpeechWhisper({ dataRoot: root, modelOrigins: ['https://one.test'] }, { assets, vadAsset, fetch: stalled, load: fakeLoader().load })
  provider.preparation.prepare()
  while (provider.snapshot().phase !== 'downloading') await new Promise((resolve) => setTimeout(resolve, 2))
  await provider.preparation.cancel()
  assert.equal(provider.snapshot().phase, 'cancelled')
  assert.equal(existsSync(join(root, 'tiny-encoder.int8.onnx')), false)
  await assert.rejects(provider.transcribe({ audio: wave(), language: 'ru' }, new AbortController().signal), /not prepared/)
  await rm(root, { recursive: true, force: true })
})

test('verified files left on disk are ready to use without a download, and a recording is recognized', async () => {
  const root = await scratch()
  const { files, assets, vadAsset } = release()
  for (const [name, bytes] of Object.entries(files)) await writeFile(join(root, name), bytes)
  const fetch = async () => {
    throw new Error('nothing should be downloaded')
  }
  const { load, seen } = fakeLoader()
  const provider = createSpeechWhisper({ dataRoot: root, modelOrigins: ['https://one.test'] }, { assets, vadAsset, fetch, load })
  await provider.inspect()
  assert.equal(provider.snapshot().phase, 'standby')

  const transcript = await provider.transcribe({ audio: wave({ frames: 8000 }), language: 'ru' }, new AbortController().signal)
  assert.equal(transcript.text, 'Привет, это проверка')
  assert.equal(transcript.audioSeconds, 0.5)
  assert.ok(transcript.inferenceSeconds >= 0)
  assert.equal(provider.snapshot().phase, 'ready')
  assert.deepEqual(seen.languages, ['ru'])
  assert.ok(seen.samples[0] instanceof Float32Array)
  assert.equal(seen.samples[0].length, 8000)

  const automatic = await provider.transcribe({ audio: wave(), language: 'auto' }, new AbortController().signal)
  assert.equal(automatic.text, 'Привет, это проверка')
  assert.deepEqual(seen.languages, ['ru', ''], 'automatic detection is an empty language')
  await rm(root, { recursive: true, force: true })
})

test('a recording without speech comes back empty rather than as a failure', async () => {
  const root = await scratch()
  const { files, assets, vadAsset } = release()
  for (const [name, bytes] of Object.entries(files)) await writeFile(join(root, name), bytes)
  const provider = createSpeechWhisper({ dataRoot: root, modelOrigins: ['https://one.test'] }, { assets, vadAsset, fetch: async () => new Response(null, { status: 500 }), load: fakeLoader(() => '').load })
  await provider.inspect()
  const transcript = await provider.transcribe({ audio: wave(), language: 'ru' }, new AbortController().signal)
  assert.equal(transcript.text, '')
  await rm(root, { recursive: true, force: true })
})

test('a recording before preparation is refused instead of downloaded on the side', async () => {
  const provider = createSpeechWhisper({ dataRoot: join(tmpdir(), 'whisper-never-created') }, { fetch: async () => new Response(null, { status: 500 }) })
  await provider.inspect()
  assert.equal(provider.snapshot().phase, 'unprepared')
  await assert.rejects(provider.transcribe({ audio: wave(), language: 'ru' }, new AbortController().signal), /not prepared/)
})

test('the origin that answers is tried first, and the others stay as a fallback', async () => {
  const { assets } = release()
  const asset = assets[0]
  const seen = []
  const fetch = async (url, init = {}) => {
    seen.push(url)
    return new Response(null, { status: url.startsWith('https://live.test') ? 200 : 404 })
  }
  const ordered = await orderSources(asset, ['https://dead.test', 'https://live.test'], 50, new AbortController().signal, fetch)
  assert.deepEqual(ordered, [`https://live.test/${asset.path}`, `https://dead.test/${asset.path}`])
  assert.equal(seen.length, 2)

  const single = await orderSources(asset, ['https://only.test'], 50, new AbortController().signal, fetch)
  assert.deepEqual(single, [`https://only.test/${asset.path}`], 'one origin needs no probe')
})

test('a harness without the speech seam is left alone', () => {
  const bare = { get: () => undefined, effect: () => undefined }
  assert.equal(registerSpeechWhisper(bare, {}), false)
})

test('a harness with the speech seam gets a provider registered on its own effect', () => {
  const registered = []
  const effects = []
  const ctx = {
    get: (name) => (name === 'speechToText' ? { register: (provider) => (registered.push(provider), async () => undefined) } : undefined),
    effect: (body, label) => (effects.push(label), body()),
  }
  assert.equal(registerSpeechWhisper(ctx, { providerId: 'whisper-test' }), true)
  assert.equal(registered.length, 1)
  assert.equal(registered[0].info.id, 'whisper-test')
  assert.ok(registered[0].info.name.includes('Whisper'))
  assert.notEqual(effects.length, 0)
})

test('the model files are the ones this release was checked against', async () => {
  const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
  assert.equal(pkg.exports['./speech-whisper'].default, './lib/speech-whisper.js')
  const { MODEL_ASSETS } = await import('../lib/speech-whisper.js')
  assert.equal(MODEL_ASSETS.length, 3)
  for (const asset of MODEL_ASSETS) {
    assert.match(asset.path, /resolve\/[0-9a-f]{40}\//, 'a commit, not a branch')
    assert.match(asset.sha256, /^[0-9a-f]{64}$/)
    assert.ok(asset.bytes > 0)
  }
})
