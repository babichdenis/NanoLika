/**
 * A long-lived Piper renderer: the model is loaded once, then every request is
 * just synthesis. Spawning a process per sentence costs a second of model load
 * each time — that was the delay between the written and the spoken answer.
 *
 * Protocol: one JSON object per line on stdin, one per line on stdout.
 *   in:  { "id": 1, "text": "…", "model": "…onnx", "tokens": "…txt", "dataDir": "…" }
 *   out: { "id": 1, "file": "/tmp/…wav" } | { "id": 1, "error": "…" }
 */
import { writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const sherpaMain = process.argv[2]
if (!sherpaMain) {
  console.error('usage: node tts-piper-server.mjs <sherpa-main>')
  process.exit(2)
}

const loaded = await import(pathToFileURL(sherpaMain).href)
const factory = loaded.OfflineTts ?? loaded.default?.OfflineTts
if (typeof factory !== 'function') {
  console.error('sherpa-onnx-node did not expose OfflineTts')
  process.exit(3)
}

/** @type {Map<string, any>} */
const engines = new Map()

function engineFor(model, tokens, dataDir) {
  const key = `${model}\0${tokens}\0${dataDir}`
  let tts = engines.get(key)
  if (tts === undefined) {
    tts = new factory({ model: { vits: { model, tokens, dataDir } }, maxNumSentences: 1, numThreads: 2, provider: 'cpu' })
    engines.set(key, tts)
  }
  return tts
}

function wave(samples, rate) {
  const buffer = Buffer.alloc(44 + samples.length * 2)
  buffer.write('RIFF', 0); buffer.writeUInt32LE(36 + samples.length * 2, 4); buffer.write('WAVE', 8)
  buffer.write('fmt ', 12); buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36); buffer.writeUInt32LE(samples.length * 2, 40)
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]))
    buffer.writeInt16LE(value < 0 ? value * 0x8000 : value * 0x7fff, 44 + i * 2)
  }
  return buffer
}

let pending = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (chunk) => {
  pending += chunk
  let index = pending.indexOf('\n')
  while (index >= 0) {
    const line = pending.slice(0, index).trim()
    pending = pending.slice(index + 1)
    if (line !== '') {
      try {
        const request = JSON.parse(line)
        const tts = engineFor(request.model, request.tokens, request.dataDir)
        if (request.warmup === true) {
          process.stdout.write(`${JSON.stringify({ id: request.id, warmed: true })}\n`)
          index = pending.indexOf('\n')
          continue
        }
        const audio = tts.generate({ text: request.text, sid: 0, speed: 1.0 })
        const file = join(tmpdir(), `nanomuse-piper-${request.id}.wav`)
        writeFileSync(file, wave(audio.samples, audio.sampleRate))
        process.stdout.write(`${JSON.stringify({ id: request.id, file })}\n`)
      } catch (error) {
        process.stdout.write(`${JSON.stringify({ id: null, error: String(error && error.message ? error.message : error) })}\n`)
      }
    }
    index = pending.indexOf('\n')
  }
})
