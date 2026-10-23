/**
 * Whisper on this computer: the recognizer that hears Russian.
 *
 * SenseVoice — the one the desktop has always had — is a Mandarin, English, Cantonese,
 * Japanese and Korean model: a Russian sentence comes back as Chinese characters, or as
 * nothing at all. Whisper is multilingual, and at the tiny size it is small enough to keep
 * beside SenseVoice on disk (104 MB, int8) and quick enough on a CPU for one spoken
 * message. Both speak the same seam (`ctx.speechToText`), so the composer's voice button
 * offers whichever the person picked and the recording never leaves the machine.
 *
 * The seam's types live in `@deepseek-ai/dsh-experimental-speech-to-text`, which this
 * bundle does not depend on — the host loads us inside its own tree — so the shapes we
 * touch are restated narrowly here, the way `web-search.ts` restates the search seam.
 *
 * The native library does the heavy lifting off the main thread: `createAsync` loads the
 * models without holding the event loop for the nine seconds whisper-tiny needs, and
 * `decodeAsync` decodes each speech segment the same way. A cold start therefore costs
 * the Host milliseconds, and a long recording never stalls it (measured on this computer:
 * 95 ms of the event loop during a load, 300 ms during a three-second decode).
 */
import { createHash } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, open, rename, rm, stat } from 'node:fs/promises'
import type { FileHandle } from 'node:fs/promises'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import type sherpaOnnx from 'sherpa-onnx-node'
import type { OfflineRecognizer, Vad } from 'sherpa-onnx-node'
import { dshHome } from './paths.ts'

/* ------------------------------------------------------------------------------------ *
 * The seam, restated
 * ------------------------------------------------------------------------------------ */

/** Ordered preparation operations the voice UI understands. */
export type SpeechPreparationStepKind = 'check' | 'model' | 'vad' | 'verify' | 'load'

/** One Host-owned preparation operation. */
export interface SpeechPreparationStep {
  readonly kind: SpeechPreparationStepKind
  readonly status: 'pending' | 'running' | 'complete' | 'failed' | 'cancelled'
  readonly startedAt?: number
}

/** Safe download diagnostics: a URL without credentials, or a local path the person chose. */
export interface SpeechDownloadFailure {
  readonly resource: string
  readonly source: string
  readonly reason: 'network' | 'dns' | 'timeout' | 'certificate' | 'http' | 'integrity' | 'storage' | 'unknown'
  readonly code?: string
  readonly status?: number
}

/** Provider facts and the current resource readiness. */
export type SpeechPreparationState = (
  | { readonly phase: 'unprepared' | 'ready' | 'standby' | 'cancelled' }
  | { readonly phase: 'downloading'; readonly resource: string; readonly completedBytes: number; readonly totalBytes?: number }
  | { readonly phase: 'checking' | 'loading' | 'waking' | 'cancelling'; readonly startedAt: number }
  | { readonly phase: 'failed'; readonly message: string; readonly download?: SpeechDownloadFailure }
) & {
  readonly step?: SpeechPreparationStepKind
  readonly steps?: readonly SpeechPreparationStep[]
}

/** Public provider facts; filesystem paths and credentials stay out of them. */
export interface SpeechProviderInfo {
  readonly id: string
  readonly name: string
  readonly location: 'host-local' | 'cloud'
  readonly languages: readonly string[]
  readonly setupEstimate?: {
    readonly recommendedDiskBytes: number
    readonly expectedMemoryBytes: number
    readonly minimumMinutes: number
    readonly maximumMinutes: number
  }
  readonly downloadSources?: readonly string[]
}

/** Download choice for one preparation task. */
export interface SpeechPreparationOptions {
  readonly downloadSource?: string
}

/** Resource preparation controls. */
export interface SpeechPreparation {
  snapshot(): SpeechPreparationState
  subscribe(listener: () => void): () => void
  prepare(options?: SpeechPreparationOptions): void
  cancel(): Promise<void>
}

/** Complete recording and an explicit language hint. */
export interface SpeechInput {
  readonly audio: Uint8Array
  readonly language: string
}

/** Final transcription; an empty string means no speech was recognized. */
export interface Transcript {
  readonly text: string
  readonly audioSeconds: number
  readonly inferenceSeconds: number
}

/** One replaceable recognizer. */
export interface SpeechProvider {
  readonly info: SpeechProviderInfo
  readonly preparation?: SpeechPreparation
  transcribe(input: SpeechInput, signal: AbortSignal): Promise<Transcript>
}

/** The one member of the speech registry this plugin calls. */
export interface SpeechService {
  register(provider: SpeechProvider): () => Promise<void>
}

/* ------------------------------------------------------------------------------------ *
 * Languages
 * ------------------------------------------------------------------------------------ */

/** What the picker offers: Russian first, the language this provider exists for. */
export const LANGUAGES = ['ru', 'en', 'auto'] as const

/**
 * The language to hand Whisper: a bare code, or nothing at all so that it decides itself.
 *
 * The seam carries whatever string the picker sent — `auto`, `ru-RU`, an empty default —
 * and Whisper wants `ru` or `""`. Whisper knows about a hundred languages, so a two-letter
 * code it was not advertised with is passed on as it came rather than refused.
 * @param hint - language the caller asked for.
 * @param fallback - language to assume when the hint is empty.
 * @returns a two-letter code, or an empty string for automatic detection.
 */
export function whisperLanguage(hint: string, fallback = 'ru'): string {
  const raw = (hint.trim() || fallback.trim()).toLowerCase().replace('_', '-')
  if (raw === '' || raw === 'auto' || raw === 'detect') return ''
  const [code] = raw.split('-')
  return code !== undefined && /^[a-z]{2}$/.test(code) ? code : ''
}

/* ------------------------------------------------------------------------------------ *
 * Recordings
 * ------------------------------------------------------------------------------------ */

const SAMPLE_RATE = 16000

/** A recording read out of its WAV container. */
export interface Wave {
  readonly samples: Float32Array
  readonly sampleRate: number
  readonly seconds: number
}

/** A recording refused before the native library sees it. */
export class SpeechInputError extends Error {}

/**
 * Read a 16-bit PCM WAV recording.
 *
 * The composer sends whatever the browser wrote with a WAV header, and every writer lays
 * its chunks out differently — `afconvert`, `ffmpeg` and Chrome each put a different set of
 * chunks around `fmt ` and `data` — so the chunks are walked rather than read at fixed
 * offsets. Only the format the seam promises is accepted: one channel of 16-bit samples.
 * @param audio - the wire bytes.
 * @param maxAudioBytes - most bytes a recording may carry.
 * @returns the samples in [-1, 1) and the recording's length.
 */
export function parseWave(audio: Uint8Array, maxAudioBytes: number): Wave {
  if (audio.byteLength > maxAudioBytes) throw new SpeechInputError(`The recording is larger than ${maxAudioBytes} bytes`)
  if (audio.byteLength < 44) throw new SpeechInputError('The recording is not a WAV file')
  const view = new DataView(audio.buffer, audio.byteOffset, audio.byteLength)
  if (tag(view, 0) !== 'RIFF' || tag(view, 8) !== 'WAVE') throw new SpeechInputError('The recording is not a WAV file')
  let format: { channels: number; sampleRate: number; bits: number } | undefined
  let data: { start: number; length: number } | undefined
  let offset = 12
  while (offset + 8 <= view.byteLength) {
    const id = tag(view, offset)
    const length = view.getUint32(offset + 4, true)
    const start = offset + 8
    if (id === 'fmt ' && length >= 16) {
      format = { channels: view.getUint16(start + 2, true), sampleRate: view.getUint32(start + 4, true), bits: view.getUint16(start + 14, true) }
    } else if (id === 'data') {
      // A truncated data chunk is the recording's business: take the samples that are there.
      data = { start, length: Math.min(length, view.byteLength - start) }
    }
    // Chunks are word-aligned, and a writer is free to pad an odd length.
    offset = start + length + (length % 2)
  }
  if (format === undefined || data === undefined) throw new SpeechInputError('The recording has no audio in it')
  if (format.channels !== 1) throw new SpeechInputError('The recording must be mono')
  if (format.sampleRate !== SAMPLE_RATE) throw new SpeechInputError(`The recording must be ${SAMPLE_RATE} Hz`)
  if (format.bits !== 16) throw new SpeechInputError('The recording must be 16-bit PCM')
  const count = Math.floor(data.length / 2)
  if (count === 0) throw new SpeechInputError('The recording has no audio in it')
  const samples = new Float32Array(count)
  for (let index = 0; index < count; index += 1) samples[index] = view.getInt16(data.start + index * 2, true) / 0x8000
  return { samples, sampleRate: format.sampleRate, seconds: count / format.sampleRate }
}

/** Four characters at `offset`, or an empty string past the end. */
function tag(view: DataView, offset: number): string {
  if (offset + 4 > view.byteLength) return ''
  return String.fromCharCode(view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2), view.getUint8(offset + 3))
}

/* ------------------------------------------------------------------------------------ *
 * The release the model comes from
 * ------------------------------------------------------------------------------------ */

/** One pinned download: the file, where it sits upstream, and what it must be. */
export interface Asset {
  readonly name: string
  /** Path inside a Hugging Face-compatible origin. */
  readonly path: string
  readonly bytes: number
  readonly sha256: string
}

/** The Whisper repository this release was checked against. */
const WHISPER_REPOSITORY = 'csukuangfj/sherpa-onnx-whisper-tiny'
/** A commit, not `main`: the hashes below are pinned to it. */
const WHISPER_REVISION = '65176e2deb88badc814a94058666cadccc29b61c'

/**
 * whisper-tiny, quantized: the pair of graphs and the token table.
 *
 * Tiny is the size that fits a laptop's CPU: 104 MB on disk, about three seconds of
 * decoding for three seconds of speech with two threads, and Russian that comes back with
 * the right words and the right punctuation. A larger Whisper reads a strong accent
 * better and is a configuration of its own.
 */
export const MODEL_ASSETS: readonly Asset[] = [
  {
    name: 'tiny-encoder.int8.onnx',
    path: `${WHISPER_REPOSITORY}/resolve/${WHISPER_REVISION}/tiny-encoder.int8.onnx`,
    bytes: 12937772,
    sha256: 'd24fb083ae3b1041fc24e97971d60e280c9342201fbb67b0ab428a8b4a51a434',
  },
  {
    name: 'tiny-decoder.int8.onnx',
    path: `${WHISPER_REPOSITORY}/resolve/${WHISPER_REVISION}/tiny-decoder.int8.onnx`,
    bytes: 89855401,
    sha256: 'd2fece8dd42771f1df975c6c0445770d0c292bf7547c2cae04a6c0cc57540925',
  },
  {
    name: 'tiny-tokens.txt',
    path: `${WHISPER_REPOSITORY}/resolve/${WHISPER_REVISION}/tiny-tokens.txt`,
    bytes: 816730,
    sha256: 'b34b360dbb493e781e479794586d661700670d65564001f23024971d1f2fa126',
  },
]

/** The speech detector: the same Silero file SenseVoice downloads. */
export const VAD_ASSET: Asset = {
  name: 'silero_vad.onnx',
  path: 'csukuangfj/vad/resolve/fba88cd2e921609e7675c3aaf51e0b9b295da4bc/silero_vad.onnx',
  bytes: 1807522,
  sha256: 'a35ebf52fd3ce5f1469b2a36158dba761bc47b973ea3382b3186ca15b1f5af28',
}

/* ------------------------------------------------------------------------------------ *
 * Configuration
 * ------------------------------------------------------------------------------------ */

export interface Config {
  /** Registration id the voice UI selects. */
  providerId: string
  /** The name the person sees in the provider picker. */
  name: string
  /** Where the verified models are kept; empty means `$DSH_HOME/speech-to-text/whisper`. */
  dataRoot: string
  /** An existing Silero VAD file; empty means the SenseVoice copy, or a download of our own. */
  vadModelPath: string
  /** Hugging Face-compatible origins, tried in this order; the first that answers is used first. */
  modelOrigins: string[]
  /** Language assumed when the caller sends no hint. */
  language: string
  /** CPU intra-operation thread count. */
  threads: number
  /** Maximum WAV bytes accepted from the composer. */
  maxAudioBytes: number
  /** Longest speech segment handed to Whisper in one go; longer speech is split by the VAD. */
  segmentSeconds: number
  /** Silero speech probability threshold. */
  vadThreshold: number
  /** Minimum speech duration the VAD keeps. */
  minSpeechSeconds: number
  /** Silence that separates two speech segments. */
  minSilenceSeconds: number
  /** Deadline for probing one origin before the next is tried. */
  modelProbeTimeoutMs: number
  /** Deadline for the download and the first model load together. */
  prepareTimeoutMs: number
  /** Minimum interval between two download progress notifications. */
  progressIntervalMs: number
}

const MAX_TIMER_DELAY_MS = 2 ** 31 - 1

export const Config: z<Config> = z.object({
  providerId: z.string().min(1).default('whisper-local').description('Registration id the voice UI selects.'),
  name: z.string().min(1).default('Whisper (локально, русский)').description('Name shown in the provider picker.'),
  dataRoot: z.string().default('').description('Directory for the verified models; empty means $DSH_HOME/speech-to-text/whisper.'),
  vadModelPath: z.string().default('').description('An existing Silero VAD file; empty reuses the SenseVoice copy or downloads one.'),
  modelOrigins: z.array(z.string().pattern(/^https?:\/\/[^/\s?#@]+\/?$/)).min(1).default(['https://huggingface.co', 'https://hf-mirror.com']).description('Hugging Face-compatible origins, tried in this order.'),
  language: z.string().default('ru').description('Language assumed when the caller sends no hint.'),
  threads: z.natural().min(1).default(2).description('CPU intra-operation thread count.'),
  maxAudioBytes: z.natural().min(44).default(4 * 1024 * 1024).description('Maximum WAV bytes accepted from the composer.'),
  segmentSeconds: z.number().min(1).max(120).default(30).description('Longest speech segment decoded in one go.'),
  vadThreshold: z.number().min(0).max(1).default(0.5).description('Silero speech probability threshold.'),
  minSpeechSeconds: z.number().min(0).default(0.25).description('Minimum speech duration the VAD keeps.'),
  minSilenceSeconds: z.number().min(0.01).default(0.5).description('Silence that separates two speech segments.'),
  modelProbeTimeoutMs: z.natural().min(1).max(MAX_TIMER_DELAY_MS).default(3000).description('Deadline for probing one origin.'),
  prepareTimeoutMs: z.natural().min(1).max(MAX_TIMER_DELAY_MS).default(30 * 60 * 1000).description('Deadline for the download and the first model load.'),
  progressIntervalMs: z.natural().min(1).max(MAX_TIMER_DELAY_MS).default(100).description('Minimum interval between download progress notifications.'),
})

/* ------------------------------------------------------------------------------------ *
 * The native recognizer
 * ------------------------------------------------------------------------------------ */

/** Silero reads 512 samples at a time at 16 kHz — 32 ms. */
const VAD_WINDOW = 512
/** Room for the longest segment plus slack, in seconds. */
const VAD_BUFFER_SECONDS = 60

/** The files a loaded recognizer needs. */
export interface ModelPaths {
  readonly encoder: string
  readonly decoder: string
  readonly tokens: string
  readonly vad: string
}

/**
 * The loaded native pair: the VAD finds the speech, Whisper reads it.
 *
 * An interface rather than a class because preparation and inference take it through a
 * seam: a test stands in for the native library, and another recognizer would only have to
 * answer these two calls.
 */
export interface LocalWhisper {
  /** Load the graph for one language hint; resolves when a recording can be decoded. */
  warm(language: string): Promise<void>
  /** @returns the text of one recording, empty when the detector hears no speech. */
  recognize(samples: Float32Array, language: string): Promise<string>
}

/** Whisper through `sherpa-onnx-node`, on the CPU. */
class SherpaWhisper implements LocalWhisper {
  private readonly recognizers = new Map<string, Promise<OfflineRecognizer>>()
  private vad: Vad | undefined

  constructor(
    private readonly sherpa: typeof sherpaOnnx,
    private readonly paths: ModelPaths,
    private readonly config: Config,
  ) {}

  async warm(language: string): Promise<void> {
    await this.recognizer(language)
  }

  async recognize(samples: Float32Array, language: string): Promise<string> {
    const recognizer = await this.recognizer(language)
    const vad = this.detector()
    vad.reset()
    for (let start = 0; start < samples.length; start += VAD_WINDOW) vad.acceptWaveform(samples.subarray(start, Math.min(start + VAD_WINDOW, samples.length)))
    vad.flush()
    const parts: string[] = []
    while (!vad.isEmpty()) {
      const segment = vad.front()
      vad.pop()
      // `front` hands out a window into the detector's ring buffer: copy before the next pop.
      const speech = Float32Array.from(segment.samples)
      if (speech.length === 0) continue
      const stream = recognizer.createStream()
      stream.acceptWaveform({ samples: speech, sampleRate: SAMPLE_RATE })
      const text = (await recognizer.decodeAsync(stream)).text.trim()
      if (text !== '') parts.push(text)
    }
    return parts.join(' ')
  }

  /** The detector, built once: its model load costs as much as a second of speech. */
  private detector(): Vad {
    return (this.vad ??= new this.sherpa.Vad(
      {
        sileroVad: {
          model: this.paths.vad,
          threshold: this.config.vadThreshold,
          minSpeechDuration: this.config.minSpeechSeconds,
          minSilenceDuration: this.config.minSilenceSeconds,
          windowSize: VAD_WINDOW,
          maxSpeechDuration: this.config.segmentSeconds,
        },
        sampleRate: SAMPLE_RATE,
        numThreads: this.config.threads,
        provider: 'cpu',
        debug: 0,
      },
      VAD_BUFFER_SECONDS,
    ))
  }

  /**
   * The recognizer for one language.
   *
   * Whisper's language is part of its model config, so another hint is another load. One
   * language is kept rather than three, because each recognizer holds about 100 MB of
   * weights and a person speaks one language at a time.
   */
  private recognizer(language: string): Promise<OfflineRecognizer> {
    const cached = this.recognizers.get(language)
    if (cached) return cached
    this.recognizers.clear()
    const pending = this.sherpa.OfflineRecognizer.createAsync({
      featConfig: { sampleRate: SAMPLE_RATE, featureDim: 80 },
      modelConfig: {
        whisper: { encoder: this.paths.encoder, decoder: this.paths.decoder, language, task: 'transcribe', tailPaddings: -1 },
        tokens: this.paths.tokens,
        numThreads: this.config.threads,
        provider: 'cpu',
        debug: 0,
      },
    })
    this.recognizers.set(language, pending)
    pending.catch(() => this.recognizers.delete(language))
    return pending
  }
}

/**
 * Load the native library and the models behind it.
 *
 * `sherpa-onnx-node` is not a dependency of this bundle: the host's tree carries it for
 * SenseVoice, and `build.mjs` keeps it external so that one copy is loaded, the host's.
 * Imported here rather than at the top, so a composition without the native library — a
 * test, a host built without voice — never touches the addon.
 */
async function createNativeWhisper(paths: ModelPaths, config: Config): Promise<LocalWhisper> {
  const loaded = await import('sherpa-onnx-node')
  const sherpa = loaded.default
  if (typeof sherpa?.OfflineRecognizer?.createAsync !== 'function') throw new Error('The native speech runtime (sherpa-onnx-node) is not available in this harness')
  return new SherpaWhisper(sherpa, paths, config)
}

/* ------------------------------------------------------------------------------------ *
 * Downloads
 * ------------------------------------------------------------------------------------ */

/** A preparation failure whose public details hold neither credentials nor raw causes. */
export class SpeechDownloadError extends Error {
  readonly download: SpeechDownloadFailure

  constructor(download: SpeechDownloadFailure, options?: ErrorOptions) {
    super(`Could not prepare ${download.resource} (${download.reason})`, options)
    this.download = download
  }
}

/** One failure, described the way the UI's localized guidance expects. */
function failureOf(error: unknown, asset: Asset, source: string): SpeechDownloadFailure {
  const code = typeof (error as { code?: unknown }).code === 'string' ? (error as { code: string }).code : undefined
  const name = (error as { name?: unknown }).name
  const base = { resource: asset.name, source }
  if (name === 'AbortError' || code === 'ABORT_ERR') return { ...base, reason: 'timeout' }
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return { ...base, reason: 'dns' }
  if (code === 'ETIMEDOUT' || code === 'UND_ERR_CONNECT_TIMEOUT' || code === 'UND_ERR_HEADERS_TIMEOUT') return { ...base, reason: 'timeout' }
  if (code !== undefined && (code.startsWith('ERR_TLS') || code.startsWith('CERT_') || code === 'UNABLE_TO_VERIFY_LEAF_SIGNATURE')) return { ...base, reason: 'certificate' }
  if (code === 'ENOSPC' || code === 'EACCES' || code === 'EROFS' || code === 'EDQUOT') return { ...base, reason: 'storage' }
  return code === undefined ? { ...base, reason: 'network' } : { ...base, reason: 'network', code }
}

/** `https://huggingface.co` + `csukuangfj/…` → `https://huggingface.co/csukuangfj/…`. */
function originUrl(origin: string, asset: Asset): string {
  return `${origin.replace(/\/+$/, '')}/${asset.path}`
}

/** `globalThis.fetch`, as much of it as this file uses. */
export type FetchLike = (url: string, init: { method?: string; signal?: AbortSignal }) => Promise<Response>

/** One HEAD probe: a source that answers is worth trying first. */
async function answers(url: string, timeoutMs: number, signal: AbortSignal, fetchLike: FetchLike): Promise<boolean> {
  try {
    return (await fetchLike(url, { method: 'HEAD', signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]) })).ok
  } catch {
    return false
  }
}

/**
 * Order the origins for one asset: the ones that answer first, the rest after them.
 * @param asset - the file about to be fetched.
 * @param origins - configured origins, in preference order.
 * @param timeoutMs - how long one probe may take.
 * @param signal - preparation cancellation.
 * @param fetchLike - the network; a test stands in for it.
 * @returns absolute URLs; with a single origin nothing is probed.
 */
export async function orderSources(asset: Asset, origins: readonly string[], timeoutMs: number, signal: AbortSignal, fetchLike: FetchLike = globalThis.fetch): Promise<string[]> {
  if (origins.length < 2) return origins.map((origin) => originUrl(origin, asset))
  const probes = await Promise.all(origins.map((origin) => answers(originUrl(origin, asset), timeoutMs, signal, fetchLike)))
  const live = origins.filter((_origin, index) => probes[index] === true)
  const slow = origins.filter((_origin, index) => probes[index] !== true)
  return [...live, ...slow].map((origin) => originUrl(origin, asset))
}

/** SHA-256 of a file, streamed so that a 90 MB graph does not sit in memory. */
async function sha256(file: string): Promise<string> {
  const hash = createHash('sha256')
  const handle = await open(file, 'r')
  try {
    for await (const chunk of handle.createReadStream()) hash.update(chunk)
  } finally {
    await handle.close().catch(() => undefined)
  }
  return hash.digest('hex')
}

/** A file of the pinned release with the pinned length. */
async function present(file: string, bytes: number): Promise<boolean> {
  try {
    return (await stat(file)).size === bytes
  } catch {
    return false
  }
}

/** The Silero model SenseVoice has already downloaded on this computer, if it is there. */
function sharedVad(home: string): string {
  return join(home, 'speech-to-text', 'sensevoice', 'models', 'silero', 'silero_vad.onnx')
}

/* ------------------------------------------------------------------------------------ *
 * The provider
 * ------------------------------------------------------------------------------------ */

/** Stand-ins a test (or a host with its own release) may pass instead of the real ones. */
export interface SpeechWhisperEnv {
  /** The downloads this release is pinned to. */
  readonly assets?: readonly Asset[]
  /** The speech detector's file. */
  readonly vadAsset?: Asset
  /** How the native recognizer is built. */
  readonly load?: (paths: ModelPaths, config: Config) => Promise<LocalWhisper>
  /** The network. */
  readonly fetch?: FetchLike
  /** The clock, so a test drives progress without waiting. */
  readonly now?: () => number
}

/** One recognizer, its preparation, and its own queue of recordings. */
export class SpeechWhisper implements SpeechProvider {
  readonly info: SpeechProviderInfo
  readonly preparation: SpeechPreparation

  private readonly settings: Config
  private readonly root: string
  private readonly assets: readonly Asset[]
  private readonly vadAsset: Asset
  private readonly load: (paths: ModelPaths, config: Config) => Promise<LocalWhisper>
  private readonly fetch: FetchLike
  private readonly now: () => number
  private readonly listeners = new Set<() => void>()

  private state: SpeechPreparationState = { phase: 'unprepared' }
  private controller: AbortController | undefined
  private task: Promise<void> | undefined
  private paths: ModelPaths | undefined
  private whisper: LocalWhisper | undefined
  /** One recording at a time: the native recognizer is not a pool. */
  private tail: Promise<unknown> = Promise.resolve()

  constructor(config: Config, env: SpeechWhisperEnv = {}) {
    this.settings = config
    this.root = config.dataRoot || join(dshHome(), 'speech-to-text', 'whisper')
    this.assets = env.assets ?? MODEL_ASSETS
    this.vadAsset = env.vadAsset ?? VAD_ASSET
    this.load = env.load ?? createNativeWhisper
    this.fetch = env.fetch ?? globalThis.fetch
    this.now = env.now ?? Date.now
    const disk = this.assets.reduce((total, asset) => total + asset.bytes, this.vadAsset.bytes)
    this.info = {
      id: config.providerId,
      name: config.name,
      location: 'host-local',
      languages: LANGUAGES,
      // The graphs are mapped by onnxruntime, so the resident set stays well under their
      // size; the minutes are a download on a home connection plus the first load.
      setupEstimate: { recommendedDiskBytes: disk, expectedMemoryBytes: 320 * 1024 * 1024, minimumMinutes: 1, maximumMinutes: 10 },
      downloadSources: config.modelOrigins,
    }
    this.preparation = {
      snapshot: () => this.state,
      subscribe: (listener) => this.subscribe(listener),
      prepare: (options) => this.prepare(options),
      cancel: () => this.cancel(),
    }
  }

  /** Preparation as the seam reads it. */
  snapshot(): SpeechPreparationState {
    return this.state
  }

  /** Observe readiness; @returns the subscription's disposer. */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /**
   * Look at what is already on disk, without downloading or loading anything.
   *
   * A size check rather than a hash: hashing 100 MB on every start of the app buys little
   * over a length that matches the pinned release. The hash is checked once, right after a
   * download, where it actually guards against a truncated or wrong file.
   */
  async inspect(): Promise<void> {
    if (this.state.phase !== 'unprepared') return
    const paths = await this.existing()
    if (paths === undefined) return
    this.paths = paths
    this.publish({ phase: 'standby' })
  }

  /**
   * Download what is missing and load the models.
   * @param options - one advertised origin to use instead of the configured order.
   */
  prepare(options: SpeechPreparationOptions = {}): void {
    if (this.task !== undefined) return
    const controller = new AbortController()
    this.controller = controller
    this.task = this.run(options, controller).finally(() => {
      this.task = undefined
      this.controller = undefined
    })
  }

  /**
   * Cancel unfinished preparation.
   *
   * A model load already under way is native and cannot be interrupted; the cancelled
   * state is published once it settles.
   * @returns after the preparation task has settled.
   */
  async cancel(): Promise<void> {
    const task = this.task
    if (task === undefined) return
    this.controller?.abort()
    await task
  }

  /**
   * Recognize one recording.
   * @param input - WAV bytes and the language hint the person picked.
   * @param signal - caller cancellation.
   * @returns the text and both durations; empty text when the VAD hears no speech.
   */
  async transcribe(input: SpeechInput, signal: AbortSignal): Promise<Transcript> {
    const wave = parseWave(input.audio, this.settings.maxAudioBytes)
    const language = whisperLanguage(input.language, this.settings.language)
    signal.throwIfAborted()
    const began = this.now()
    const text = await this.queued(signal, async () => {
      const whisper = await this.ready(language, signal)
      signal.throwIfAborted()
      return whisper.recognize(wave.samples, language)
    })
    return { text, audioSeconds: round(wave.seconds), inferenceSeconds: round((this.now() - began) / 1000) }
  }

  /* ---------------------------------------------------------------------------------- *
   * Preparation
   * ---------------------------------------------------------------------------------- */

  /** The one preparation task: check, fetch, verify, load. */
  private async run(options: SpeechPreparationOptions, controller: AbortController): Promise<void> {
    const startedAt = this.now()
    const deadline = AbortSignal.timeout(this.settings.prepareTimeoutMs)
    const signal = AbortSignal.any([controller.signal, deadline])
    this.publish({ phase: 'checking', startedAt, step: 'check' })
    try {
      const chosen = options.downloadSource
      if (chosen !== undefined && !this.settings.modelOrigins.includes(chosen)) throw new Error(`${chosen} is not a download source this recognizer offers`)
      const paths = await this.obtain(signal, chosen)
      this.publish({ phase: 'loading', startedAt, step: 'load' })
      const whisper = await this.loadFor(paths)
      signal.throwIfAborted()
      this.paths = paths
      this.whisper = whisper
      this.publish({ phase: 'ready' })
    } catch (error) {
      if (controller.signal.aborted) {
        this.publish({ phase: 'cancelled' })
      } else if (deadline.aborted) {
        this.publish({ phase: 'failed', message: `Preparing the local Whisper model took longer than ${Math.round(this.settings.prepareTimeoutMs / 1000)} seconds` })
      } else if (error instanceof SpeechDownloadError) {
        this.publish({ phase: 'failed', message: messageOf(error), download: error.download })
      } else {
        this.publish({ phase: 'failed', message: messageOf(error) })
      }
    }
  }

  /** Build the native recognizer for the default language and warm it up. */
  private async loadFor(paths: ModelPaths): Promise<LocalWhisper> {
    const whisper = await this.load(paths, this.settings)
    await whisper.warm(whisperLanguage('', this.settings.language))
    return whisper
  }

  /** Paths of whatever verified files are already on disk, or nothing. */
  private async existing(): Promise<ModelPaths | undefined> {
    const [encoder, decoder, tokens] = this.assets
    if (encoder === undefined || decoder === undefined || tokens === undefined) return undefined
    for (const asset of [encoder, decoder, tokens]) if (!(await present(join(this.root, asset.name), asset.bytes))) return undefined
    const vad = await this.existingVad()
    if (vad === undefined) return undefined
    return { encoder: join(this.root, encoder.name), decoder: join(this.root, decoder.name), tokens: join(this.root, tokens.name), vad }
  }

  /** The Silero file in use: configured, borrowed from SenseVoice, or our own copy. */
  private async existingVad(): Promise<string | undefined> {
    const configured = this.settings.vadModelPath
    if (configured !== '') return existsSync(configured) ? configured : undefined
    const shared = sharedVad(dshHome())
    if (await present(shared, this.vadAsset.bytes)) return shared
    const own = join(this.root, this.vadAsset.name)
    return (await present(own, this.vadAsset.bytes)) ? own : undefined
  }

  /** Download whatever is missing and return the four paths. */
  private async obtain(signal: AbortSignal, chosen: string | undefined): Promise<ModelPaths> {
    await mkdir(this.root, { recursive: true })
    const files: string[] = []
    for (const asset of this.assets) {
      const target = join(this.root, asset.name)
      files.push((await present(target, asset.bytes)) ? target : await this.fetchAsset(asset, target, 'model', signal, chosen))
    }
    const [encoder, decoder, tokens] = files
    if (encoder === undefined || decoder === undefined || tokens === undefined) throw new Error('This release pins no Whisper model')
    return { encoder, decoder, tokens, vad: await this.obtainVad(signal, chosen) }
  }

  /** The speech detector: the person's own file, the SenseVoice copy, or a download. */
  private async obtainVad(signal: AbortSignal, chosen: string | undefined): Promise<string> {
    const configured = this.settings.vadModelPath
    if (configured !== '') {
      if (!existsSync(configured)) throw new Error(`The configured Silero VAD model is not on disk: ${configured}`)
      return configured
    }
    const shared = sharedVad(dshHome())
    if (await present(shared, this.vadAsset.bytes)) return shared
    const own = join(this.root, this.vadAsset.name)
    return (await present(own, this.vadAsset.bytes)) ? own : this.fetchAsset(this.vadAsset, own, 'vad', signal, chosen)
  }

  /**
   * Fetch one pinned file, trying the origins in order.
   * @param asset - the pinned identity.
   * @param target - where the verified file belongs.
   * @param kind - which step the UI shows.
   * @param signal - preparation cancellation.
   * @param chosen - one origin the person picked, which has no fallback.
   * @returns the target path.
   */
  private async fetchAsset(asset: Asset, target: string, kind: 'model' | 'vad', signal: AbortSignal, chosen: string | undefined): Promise<string> {
    const sources = chosen === undefined ? await orderSources(asset, this.settings.modelOrigins, this.settings.modelProbeTimeoutMs, signal, this.fetch) : [originUrl(chosen, asset)]
    let failure: SpeechDownloadFailure = { resource: asset.name, source: sources[0] ?? asset.path, reason: 'unknown' }
    for (const source of sources) {
      try {
        await this.fetchInto(asset, source, target, kind, signal)
        return target
      } catch (error) {
        if (signal.aborted) throw error
        failure = error instanceof SpeechDownloadError ? error.download : failureOf(error, asset, source)
        // A chosen origin is used without fallback, as the seam promises.
        if (chosen !== undefined) break
      }
    }
    throw new SpeechDownloadError(failure)
  }

  /** Stream one file to disk, reporting bytes as they arrive, then verify and publish it. */
  private async fetchInto(asset: Asset, source: string, target: string, kind: 'model' | 'vad', signal: AbortSignal): Promise<void> {
    const partial = `${target}.part`
    this.publish({ phase: 'downloading', resource: asset.name, completedBytes: 0, totalBytes: asset.bytes, step: kind })
    let handle: FileHandle | undefined
    try {
      const response = await this.fetch(source, { signal })
      if (!response.ok) throw new SpeechDownloadError({ resource: asset.name, source, reason: 'http', status: response.status })
      if (response.body === null) throw new SpeechDownloadError({ resource: asset.name, source, reason: 'unknown' })
      const totalBytes = Number(response.headers.get('content-length') ?? '') || asset.bytes
      handle = await open(partial, 'w')
      let completedBytes = 0
      let reportedAt = this.now()
      const reader = response.body.getReader()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        await handle.write(value)
        completedBytes += value.length
        if (this.now() - reportedAt >= this.settings.progressIntervalMs) {
          reportedAt = this.now()
          this.publish({ phase: 'downloading', resource: asset.name, completedBytes, totalBytes, step: kind })
        }
      }
      await handle.close()
      handle = undefined
      this.publish({ phase: 'downloading', resource: asset.name, completedBytes, totalBytes, step: 'verify' })
      if (completedBytes !== asset.bytes) throw new SpeechDownloadError({ resource: asset.name, source, reason: 'integrity', code: `expected ${asset.bytes} bytes, received ${completedBytes}` })
      if ((await sha256(partial)) !== asset.sha256) throw new SpeechDownloadError({ resource: asset.name, source, reason: 'integrity', code: 'sha256' })
      await rename(partial, target)
    } catch (error) {
      await handle?.close().catch(() => undefined)
      await rm(partial, { force: true })
      if (error instanceof SpeechDownloadError || signal.aborted) throw error
      throw new SpeechDownloadError(failureOf(error, asset, source), { cause: error })
    }
  }

  /* ---------------------------------------------------------------------------------- *
   * Inference
   * ---------------------------------------------------------------------------------- */

  /** One recording at a time, in arrival order, each cancelled before it starts if asked. */
  private queued<T>(signal: AbortSignal, job: () => Promise<T>): Promise<T> {
    const run = this.tail.then(async () => {
      signal.throwIfAborted()
      return job()
    })
    this.tail = run.catch(() => undefined)
    return run
  }

  /** The loaded recognizer for one language, loading it if preparation left it on disk. */
  private async ready(language: string, signal: AbortSignal): Promise<LocalWhisper> {
    // Preparation started by the page: wait for it rather than race it.
    if (this.task !== undefined) await this.task
    if (this.whisper !== undefined) {
      await this.whisper.warm(language)
      return this.whisper
    }
    if (this.state.phase === 'failed') throw new Error(`The local Whisper model is not ready: ${this.state.message}`)
    if (this.state.phase !== 'standby' && this.state.phase !== 'ready') throw new Error('The local Whisper model is not prepared yet')
    const paths = this.paths ?? (await this.existing())
    if (paths === undefined) throw new Error('The local Whisper model is missing; prepare it in the voice settings')
    this.publish({ phase: 'loading', startedAt: this.now(), step: 'load' })
    try {
      const whisper = await this.loadFor(paths)
      signal.throwIfAborted()
      this.paths = paths
      this.whisper = whisper
      this.publish({ phase: 'ready' })
      if (language !== whisperLanguage('', this.settings.language)) await whisper.warm(language)
      return whisper
    } catch (error) {
      this.publish({ phase: 'failed', message: messageOf(error) })
      throw error
    }
  }

  /* ---------------------------------------------------------------------------------- *
   * State
   * ---------------------------------------------------------------------------------- */

  private publish(state: SpeechPreparationState): void {
    this.state = state
    for (const listener of this.listeners) {
      try {
        listener()
      } catch {
        // A listener of a closed page must not stop preparation.
      }
    }
  }
}

/**
 * Build a provider from a partial configuration: every field of the schema has a default,
 * so what is left out is a choice already made.
 */
export function createSpeechWhisper(config: Partial<Config> = {}, env: SpeechWhisperEnv = {}): SpeechWhisper {
  return new SpeechWhisper(Config(config as Config), env)
}

/**
 * Register the provider with a Host that has the speech seam.
 *
 * A composition without voice input — a headless one, a test — has no `speechToText`, and
 * then there is nothing to register with: the rest of the bundle carries on.
 * @param ctx - the Host context.
 * @param config - the provider's configuration.
 * @returns whether a provider was registered.
 */
export function registerSpeechWhisper(ctx: Context, config: Partial<Config>): boolean {
  const speech = ctx.get('speechToText') as SpeechService | undefined
  if (speech === undefined) return false
  const provider = createSpeechWhisper(config)
  ctx.effect(() => {
    void provider.inspect()
    const dispose = speech.register(provider)
    return () => {
      void dispose()
    }
  }, 'nanomuse speech: the local Whisper recognizer')
  return true
}

/** A duration the seam can carry: three decimals are milliseconds. */
function round(seconds: number): number {
  return Math.round(seconds * 1000) / 1000
}

/** What to tell the person, without a stack trace. */
function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
