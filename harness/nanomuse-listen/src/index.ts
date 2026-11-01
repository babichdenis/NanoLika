/**
 * The host half of continuous listening and spoken answers.
 *
 * - `POST /nanomuse/voice` — WAV as base64 in, text out (the harness's local Whisper).
 * - `POST /nanomuse/speak` — text in, WAV out, rendered by the system voice; the page
 *   plays it, because a `say` started inside the packaged app is silent on this Mac.
 * - `GET|POST /nanomuse/listen/settings` — the chosen voice and whether answers are read
 *   aloud, kept in `$DSH_HOME/nanomuse/listen.json` so they survive a restart.
 * - `GET /nanomuse/listen/voices` — the voices this computer actually has.
 */
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-host-webserver'
import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

export const name = 'nanomuse-listen'

/** The routes mount on the host's web server and speak to the harness's speech registry. */
export const inject = ['webServer', 'speechToText']

const VOICE_PREFIX = '/nanomuse/voice'
const SPEAK_PREFIX = '/nanomuse/speak'
const SETTINGS_PREFIX = '/nanomuse/listen'
/** 16 MB: minutes of speech as base64, far above the harness's 64 KB JSON default. */
const LIMIT = 16 * 1024 * 1024

interface SpeechGateway {
  resolve(request: { audio: Uint8Array; providerId?: string; language?: string }): unknown
  transcribe(spec: unknown, signal: AbortSignal): Promise<{ text: string }>
  /** The recognizers this host carries and the current default. */
  snapshot(): {
    providers: Array<{ id: string; name: string; location: string; languages: readonly string[]; preparation: { phase: string } }>
    selection: { providerId: string; language: string }
  }
  configure(patch: { providerId?: string; language?: string }): Promise<void>
  prepare(id: string): void
}

interface Settings {
  voice: string
  answers: boolean
  /** `piper` is the local neural voice; `silero` and `cloud` are offered but not installed. */
  tts: 'system' | 'piper' | 'silero' | 'cloud'
  /** The cloud speech model (`voice`, `voice-hd`, `bh-tts-1`, `bh-tts-1-hd`). */
  ttsModel: string
  /** The cloud voice name (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`). */
  cloudVoice: string
  /** The Piper model directory under `speech/tts` (e.g. `vits-piper-ru_RU-irina-medium`). */
  piperVoice: string
  /** `local` uses the harness's recognizers; `cloud` sends the WAV to NanoLika Cloud. */
  stt: 'local' | 'cloud'
  /** The cloud recognizer's model id (ear-ru, ear-turbo, …). */
  sttModel: string
  /** Fall back to the local recognizer when the cloud answers with an error. */
  sttFallback: boolean
}

const DEFAULTS: Settings = { voice: 'Milena', answers: false, tts: 'system', ttsModel: 'voice', cloudVoice: 'alloy', piperVoice: 'vits-piper-ru_RU-irina-medium', stt: 'local', sttModel: 'ear-ru', sttFallback: true }

/** The Piper voices the app can fetch on demand: the id is the release archive and the folder. */
const PIPER_VOICES = [
  { id: 'vits-piper-ru_RU-irina-medium', title: 'Ирина — русский, женский', lang: 'ru' },
  { id: 'vits-piper-ru_RU-denis-medium', title: 'Денис — русский, мужской', lang: 'ru' },
  { id: 'vits-piper-ru_RU-dmitri-medium', title: 'Дмитрий — русский, мужской', lang: 'ru' },
  { id: 'vits-piper-ru_RU-ruslan-medium', title: 'Руслан — русский, мужской', lang: 'ru' },
  { id: 'vits-piper-en_US-lessac-medium', title: 'Lessac — английский, женский', lang: 'en' },
  { id: 'vits-piper-en_US-amy-medium', title: 'Amy — английский, женский', lang: 'en' },
]

/** What the last cloud probe saw: the switcher uses it to skip a dead provider. */
interface CloudHealth {
  ok: boolean
  checkedAt: number
  latencyMs: number
  error: string
}
let cloudHealth: CloudHealth = { ok: false, checkedAt: 0, latencyMs: 0, error: 'not checked yet' }

/** How long a failed probe keeps the cloud out of the chain. */
const CLOUD_RETRY_MS = 60_000

/** The local neural voice: a Piper (VITS) model that speaks Russian. */
const PIPER_DIR = 'speech/tts/vits-piper-ru_RU-irina-medium'

/** How many voices one list may carry: the picker is a shortlist, not a catalogue. */
const VOICE_LIMIT = 40

function home(): string {
  return process.env.DSH_HOME !== undefined && process.env.DSH_HOME !== '' ? process.env.DSH_HOME : join(homedir(), '.dsh')
}

function settingsPath(): string {
  return join(home(), 'nanomuse', 'listen.json')
}

async function loadSettings(): Promise<Settings> {
  try {
    const raw = JSON.parse(await readFile(settingsPath(), 'utf8')) as Partial<Settings>
    return {
      voice: typeof raw.voice === 'string' && raw.voice !== '' ? raw.voice : DEFAULTS.voice,
      answers: raw.answers === true,
      tts: raw.tts === 'piper' || raw.tts === 'silero' || raw.tts === 'cloud' ? raw.tts : 'system',
      ttsModel: typeof raw.ttsModel === 'string' && raw.ttsModel !== '' ? raw.ttsModel : DEFAULTS.ttsModel,
      cloudVoice: typeof raw.cloudVoice === 'string' && raw.cloudVoice !== '' ? raw.cloudVoice : DEFAULTS.cloudVoice,
      piperVoice: typeof raw.piperVoice === 'string' && raw.piperVoice !== '' ? raw.piperVoice : DEFAULTS.piperVoice,
      stt: raw.stt === 'cloud' ? 'cloud' : 'local',
      sttModel: typeof raw.sttModel === 'string' && raw.sttModel !== '' ? raw.sttModel : DEFAULTS.sttModel,
      sttFallback: raw.sttFallback !== false,
    }
  } catch {
    return { ...DEFAULTS }
  }
}

async function saveSettings(patch: Partial<Settings>): Promise<Settings> {
  const next = { ...(await loadSettings()), ...patch }
  const file = settingsPath()
  await mkdir(dirname(file), { recursive: true })
  await writeFile(file, `${JSON.stringify(next, null, 2)}\n`, 'utf8')
  return next
}

export function apply(ctx: Context): void {
  const handler = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    if (req.method !== 'POST') return send(res, 405, { error: { code: 'method', message: 'POST only' } })
    try {
      const body = await readJson(req, LIMIT)
      const audioBase64 = typeof body.audio === 'string' ? body.audio : ''
      const language = typeof body.language === 'string' ? body.language : 'ru'
      if (audioBase64 === '') return send(res, 400, { error: { code: 'bad_request', message: 'audio is required' } })
      const audio = new Uint8Array(Buffer.from(audioBase64, 'base64'))
      const saved = await loadSettings()
      const transcription = await transcribeWithChain(ctx, audio, saved, language, req)
      send(res, 200, { text: transcription.text, via: transcription.via })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.warn('nanomuse-listen: transcription failed: %s', message)
      send(res, 500, { error: { code: 'internal', message } })
    }
  }
  ctx.effect(() => ctx.webServer.register({ kind: 'prefix', path: VOICE_PREFIX, handler }), 'nanomuse-listen: the voice route')

  const speak = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    if (req.method !== 'POST') return send(res, 405, { error: { code: 'method', message: 'POST only' } })
    try {
      const body = await readJson(req, 64 * 1024)
      const text = typeof body.text === 'string' ? body.text.trim() : ''
      if (text === '') return send(res, 400, { error: { code: 'bad_request', message: 'text is required' } })
      const saved = await loadSettings()
      const chosen = typeof body.provider === 'string' ? body.provider : saved.tts
      const voice = typeof body.voice === 'string' && body.voice !== '' ? body.voice : saved.voice
      // One voice for the whole answer: a failover mid-sentence would change the
      // speaker between sentences, which sounds broken. The chosen engine answers
      // or reports the error; the person picks another one in Settings.
      const chain = [chosen]
      let lastError: unknown
      for (const provider of chain) {
        try {
          if (provider === 'piper') {
            const audio = await piperSpeak(text.slice(0, 1200), saved.piperVoice)
            return send(res, 200, { audio: audio.toString('base64'), mime: 'audio/wav', voice: saved.piperVoice, provider: 'piper' })
          }
          if (provider === 'system') {
            const dir = await mkdtemp(join(tmpdir(), 'nanomuse-speak-'))
            const file = join(dir, 'say.wav')
            try {
              await run('say', ['-v', voice, '-o', file, '--data-format=LEI16@22050', '--', text.slice(0, 1200)])
              const audio = await readFile(file)
              return send(res, 200, { audio: audio.toString('base64'), mime: 'audio/wav', voice, provider: 'system' })
            } finally {
              await rm(dir, { recursive: true, force: true }).catch(() => undefined)
            }
          }
          if (provider === 'cloud') {
            const audio = await cloudSpeak(text.slice(0, 1200), saved.ttsModel, saved.cloudVoice)
            return send(res, 200, { audio: audio.toString('base64'), mime: 'audio/wav', voice: saved.cloudVoice, provider: 'cloud' })
          }
          throw new Error('Silero не установлен')
        } catch (error) {
          lastError = error
        }
      }
      throw lastError ?? new Error('Ни один голос не ответил')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.warn('nanomuse-listen: speech synthesis failed: %s', message)
      send(res, 500, { error: { code: 'internal', message } })
    }
  }
  ctx.effect(() => ctx.webServer.register({ kind: 'prefix', path: SPEAK_PREFIX, handler: speak }), 'nanomuse-listen: the speak route')

  const settings = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1')
    const route = url.pathname.slice(SETTINGS_PREFIX.length) || '/'
    try {
      if (req.method === 'GET' && route === '/settings') return send(res, 200, await loadSettings())
      if (req.method === 'POST' && route === '/settings') {
        const body = await readJson(req, 8 * 1024)
        const patch: Partial<Settings> = {}
        if (typeof body.voice === 'string' && body.voice !== '') patch.voice = body.voice
        if (typeof body.answers === 'boolean') patch.answers = body.answers
        if (body.tts === 'system' || body.tts === 'piper' || body.tts === 'silero' || body.tts === 'cloud') patch.tts = body.tts
        if (typeof body.ttsModel === 'string' && body.ttsModel !== '') patch.ttsModel = body.ttsModel
        if (typeof body.cloudVoice === 'string' && body.cloudVoice !== '') patch.cloudVoice = body.cloudVoice
        if (typeof body.piperVoice === 'string' && body.piperVoice !== '') patch.piperVoice = body.piperVoice
        if (body.stt === 'local' || body.stt === 'cloud') patch.stt = body.stt
        if (typeof body.sttModel === 'string' && body.sttModel !== '') patch.sttModel = body.sttModel
        if (typeof body.sttFallback === 'boolean') patch.sttFallback = body.sttFallback
        return send(res, 200, await saveSettings(patch))
      }
      if (req.method === 'GET' && route === '/voices') return send(res, 200, { voices: await listVoices() })
      if (req.method === 'GET' && route === '/tts/voices') {
        const saved = await loadSettings()
        const installed = new Set(await installedPiper())
        return send(res, 200, {
          selected: saved.piperVoice,
          voices: PIPER_VOICES.map((voice) => ({ ...voice, installed: installed.has(voice.id) })),
        })
      }
      if (req.method === 'POST' && route === '/warmup') {
        const saved = await loadSettings()
        if (saved.tts === 'piper') void piperWarmup(saved.piperVoice)
        return send(res, 200, { ok: true })
      }
      if (req.method === 'POST' && route === '/tts/install') {
        const body = await readJson(req, 8 * 1024)
        const id = typeof body.id === 'string' ? body.id : ''
        const voice = PIPER_VOICES.find((item) => item.id === id)
        if (voice === undefined) return send(res, 400, { error: { code: 'bad_request', message: 'unknown voice' } })
        await installPiper(voice.id)
        return send(res, 200, { id: voice.id, installed: true })
      }
      if (req.method === 'GET' && route === '/health') {
        const saved = await loadSettings()
        return send(res, 200, {
          cloud: cloudHealth,
          stt: saved.stt === 'cloud' && cloudHealth.ok ? saved.sttModel : 'local',
          tts: saved.tts,
        })
      }
      if (req.method === 'GET' && route === '/tts') {
        return send(res, 200, {
          providers: [
            { id: 'system', name: 'Системный голос', local: true, ready: true, default: (await loadSettings()).tts === 'system' },
            { id: 'piper', name: 'Piper Ирина (нейроголос, локально)', local: true, ready: await piperReady(), default: (await loadSettings()).tts === 'piper' },
            { id: 'cloud', name: 'NanoLika Cloud (облако)', local: false, ready: (await loadSettings()).tts === 'cloud', default: (await loadSettings()).tts === 'cloud' },
          ],
        })
      }
      if (req.method === 'GET' && route === '/stt') {
        const speech = ctx.get('speechToText') as SpeechGateway | undefined
        if (speech === undefined) return send(res, 503, { error: { code: 'not_ready', message: 'Speech recognition not available' } })
        return send(res, 200, speech.snapshot())
      }
      if (req.method === 'POST' && route === '/stt') {
        const speech = ctx.get('speechToText') as SpeechGateway | undefined
        if (speech === undefined) return send(res, 503, { error: { code: 'not_ready', message: 'Speech recognition not available' } })
        const body = await readJson(req, 8 * 1024)
        const patch: { providerId?: string; language?: string } = {}
        if (typeof body.providerId === 'string' && body.providerId !== '') patch.providerId = body.providerId
        if (typeof body.language === 'string') patch.language = body.language
        await speech.configure(patch)
        return send(res, 200, speech.snapshot())
      }
      if (req.method === 'POST' && route === '/stt/prepare') {
        const speech = ctx.get('speechToText') as SpeechGateway | undefined
        if (speech === undefined) return send(res, 503, { error: { code: 'not_ready', message: 'Speech recognition not available' } })
        const body = await readJson(req, 8 * 1024)
        const id = typeof body.providerId === 'string' ? body.providerId : ''
        if (id === '') return send(res, 400, { error: { code: 'bad_request', message: 'providerId is required' } })
        speech.prepare(id)
        return send(res, 200, { ok: true })
      }
      send(res, 404, { error: { code: 'not_found', message: 'No such route' } })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      ctx.logger.warn('nanomuse-listen: settings failed: %s', message)
      send(res, 500, { error: { code: 'internal', message } })
    }
  }
  ctx.effect(() => ctx.webServer.register({ kind: 'prefix', path: SETTINGS_PREFIX, handler: settings }), 'nanomuse-listen: the settings routes')

  // The switcher's health check: a dead relay is skipped for a minute instead of
  // costing every phrase a timeout before the local recognizer takes over.
  void probeCloud()
  const probeTimer = setInterval(() => {
    void probeCloud()
  }, CLOUD_RETRY_MS)
  ctx.effect(() => () => clearInterval(probeTimer), 'nanomuse-listen: cloud probe')
}

/** Ask the relay whether the account key still works; cheap and bounded. */
async function probeCloud(): Promise<CloudHealth> {
  const started = Date.now()
  try {
    const token = await cloudToken()
    if (token === '') throw new Error('Аккаунт NanoLika Cloud не подключён')
    const base = process.env.NANOMUSE_CLOUD_URL ?? 'https://cloud.nanolika.ru'
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 4000)
    try {
      const res = await fetch(`${base.replace(/\/$/, '')}/v1/models`, {
        headers: { authorization: `Bearer ${token}` },
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(res.status === 401 ? 'ключ аккаунта недействителен' : `облако ответило ${res.status}`)
    } finally {
      clearTimeout(timer)
    }
    cloudHealth = { ok: true, checkedAt: Date.now(), latencyMs: Date.now() - started, error: '' }
  } catch (error) {
    cloudHealth = {
      ok: false,
      checkedAt: Date.now(),
      latencyMs: Date.now() - started,
      error: error instanceof Error ? error.message : String(error),
    }
  }
  return cloudHealth
}

/** Render text with NanoLika Cloud (OpenAI-compatible /v1/audio/speech). */
async function cloudSpeak(text: string, model: string, voice: string): Promise<Buffer> {
  const token = await cloudToken()
  if (token === '') throw new Error('Аккаунт NanoLika Cloud не подключён')
  const base = process.env.NANOMUSE_CLOUD_URL ?? 'https://cloud.nanolika.ru'
  const res = await fetch(`${base.replace(/\/$/, '')}/v1/audio/speech`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ model, input: text, voice }),
  })
  if (!res.ok) {
    const answer = (await res.json().catch(() => ({}))) as { error?: { message?: string } }
    throw new Error(answer.error?.message ?? `облако ответило ${res.status}`)
  }
  return Buffer.from(await res.arrayBuffer())
}

/** Transcribe with the chosen recognizer, falling back to the local one. */
async function transcribeWithChain(
  ctx: Context,
  audio: Uint8Array,
  saved: Settings,
  language: string,
  req: IncomingMessage,
): Promise<{ text: string; via: string }> {
  const cloudAlive = cloudHealth.ok || Date.now() - cloudHealth.checkedAt > CLOUD_RETRY_MS
  const wanted = saved.stt === 'cloud' && cloudAlive ? ['cloud'] : ['local']
  const chain = saved.sttFallback ? [...wanted, 'local'] : wanted
  let lastError: unknown
  for (const step of chain) {
    try {
      if (step === 'cloud') return { text: await cloudTranscribe(audio, saved.sttModel, language), via: saved.sttModel }
      const speech = ctx.get('speechToText') as SpeechGateway | undefined
      if (speech === undefined) throw new Error('Speech recognition not available')
      const controller = new AbortController()
      req.on('close', () => controller.abort())
      let spec: unknown
      try {
        spec = speech.resolve({ audio, providerId: 'whisper-local', language })
      } catch {
        spec = speech.resolve({ audio, language })
      }
      const transcript = await speech.transcribe(spec, controller.signal)
      return { text: transcript.text, via: 'whisper-local' }
    } catch (error) {
      lastError = error
    }
  }
  throw lastError ?? new Error('Распознавание не ответило')
}

/** The account key the desktop stores for the relay, or an empty string. */
async function cloudToken(): Promise<string> {
  try {
    const raw = await readFile(join(home(), '.credentials.yaml'), 'utf8')
    const match = /NANOMUSE_CLOUD_TOKEN["']?\s*:\s*["']?([A-Za-z0-9_\-]+)/.exec(raw)
    return match?.[1] ?? ''
  } catch {
    return ''
  }
}

/** Transcribe one WAV with NanoLika Cloud (OpenAI-compatible /v1/audio/transcriptions). */
async function cloudTranscribe(audio: Uint8Array, model: string, language: string): Promise<string> {
  const token = await cloudToken()
  if (token === '') throw new Error('Аккаунт NanoLika Cloud не подключён')
  const base = process.env.NANOMUSE_CLOUD_URL ?? 'https://cloud.nanolika.ru'
  const form = new FormData()
  form.append('model', model)
  form.append('language', language)
  form.append('file', new Blob([audio], { type: 'audio/wav' }), 'voice.wav')
  const res = await fetch(`${base.replace(/\/$/, '')}/v1/audio/transcriptions`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}` },
    body: form,
  })
  const answer = (await res.json().catch(() => ({}))) as { text?: string; error?: { message?: string } }
  if (!res.ok) throw new Error(answer.error?.message ?? `облако ответило ${res.status}`)
  return (answer.text ?? '').trim()
}

/** Whether the Piper model is on disk. */
async function piperReady(): Promise<boolean> {
  try {
    await readFile(join(home(), PIPER_DIR, 'ru_RU-irina-medium.onnx'))
    return true
  } catch {
    return false
  }
}

/** Load sherpa-onnx-node from the harness's own tree: the packaged app carries it for SenseVoice. */
async function loadSherpa(): Promise<{ OfflineTts: new (config: unknown) => { generate(request: unknown): { samples: Float32Array; sampleRate: number } } }> {
  try {
    return (await import('sherpa-onnx-node')) as never
  } catch {
    const bin = process.argv[1] ?? ''
    const entry = join(dirname(bin), '..', '..', '..', 'sherpa-onnx-node', 'sherpa-onnx.js')
    return (await import(pathToFileURL(entry).href)) as never
  }
}

/** The Piper model folders already on disk. */
async function installedPiper(): Promise<string[]> {
  try {
    const { readdir } = await import('node:fs/promises')
    const entries = await readdir(join(home(), 'speech/tts'), { withFileTypes: true })
    return entries.filter((entry) => entry.isDirectory() && entry.name.startsWith('vits-piper-')).map((entry) => entry.name)
  } catch {
    return []
  }
}

/** Download and unpack one Piper voice into `speech/tts`; the archive is the release asset. */
async function installPiper(id: string): Promise<void> {
  const root = join(home(), 'speech/tts')
  const target = join(root, id)
  if ((await installedPiper()).includes(id)) return
  const { mkdir } = await import('node:fs/promises')
  await mkdir(root, { recursive: true })
  const archive = join(tmpdir(), `${id}.tar.bz2`)
  const url = `https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${id}.tar.bz2`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`не удалось скачать голос: ${res.status}`)
  const { writeFile } = await import('node:fs/promises')
  await writeFile(archive, Buffer.from(await res.arrayBuffer()))
  try {
    await run('tar', ['-xjf', archive, '-C', root])
  } finally {
    const { rm } = await import('node:fs/promises')
    await rm(archive, { force: true }).catch(() => undefined)
  }
  const { stat } = await import('node:fs/promises')
  await stat(join(target, 'tokens.txt'))
}

/** One long-lived Piper renderer: the model loads once, then each sentence is fast. */
interface PiperServer {
  child: import('node:child_process').ChildProcess
  pending: Map<number, { resolve: (file: string) => void; reject: (error: Error) => void }>
  nextId: number
}
let piperServer: PiperServer | null = null

function piperRunner(): PiperServer {
  if (piperServer !== null) return piperServer
  const bin = process.argv[1] ?? ''
  const modules = join(dirname(bin), '..', '..', '..')
  const sherpaMain = join(modules, 'sherpa-onnx-node', 'sherpa-onnx.js')
  const script = new URL('../tts-piper-server.mjs', import.meta.url).pathname
  const child = spawn('node', [script, sherpaMain], { stdio: ['pipe', 'pipe', 'pipe'] })
  const pending = new Map<number, { resolve: (file: string) => void; reject: (error: Error) => void }>()
  let buffer = ''
  child.stdout?.setEncoding('utf8')
  child.stdout?.on('data', (chunk: string) => {
    buffer += chunk
    let index = buffer.indexOf('\n')
    while (index >= 0) {
      const line = buffer.slice(0, index).trim()
      buffer = buffer.slice(index + 1)
      index = buffer.indexOf('\n')
      if (line === '') continue
      try {
        const answer = JSON.parse(line) as { id?: number; file?: string; error?: string }
        if (typeof answer.id !== 'number') continue
        const waiter = pending.get(answer.id)
        if (waiter === undefined) continue
        pending.delete(answer.id)
        if (answer.file !== undefined) waiter.resolve(answer.file)
        else waiter.reject(new Error(answer.error ?? 'piper failed'))
      } catch {
        // a malformed line is not a response; keep reading
      }
    }
  })
  const fail = () => {
    for (const waiter of pending.values()) waiter.reject(new Error('синтез Piper остановился'))
    pending.clear()
    piperServer = null
  }
  child.on('exit', fail)
  child.on('error', fail)
  piperServer = { child, pending, nextId: 1 }
  return piperServer
}

/** Load the chosen Piper model ahead of the first sentence. */
async function piperWarmup(voice: string): Promise<void> {
  const dir = join(home(), 'speech/tts', voice)
  const { readdir } = await import('node:fs/promises')
  const files = await readdir(dir).catch(() => [] as string[])
  const model = files.find((file) => file.endsWith('.onnx'))
  if (model === undefined) return
  const runner = piperRunner()
  const id = runner.nextId++
  await new Promise<void>((resolve) => {
    runner.pending.set(id, { resolve: () => resolve(), reject: () => resolve() })
    runner.child.stdin?.write(`${JSON.stringify({
      id,
      warmup: true,
      model: join(dir, model),
      tokens: join(dir, 'tokens.txt'),
      dataDir: join(dir, 'espeak-ng-data'),
    })}\n`)
  })
}

/** Render text with the chosen local Piper voice and return WAV bytes. */
async function piperSpeak(text: string, voice = 'vits-piper-ru_RU-irina-medium'): Promise<Buffer> {
  const dir = join(home(), 'speech/tts', voice)
  const { readdir } = await import('node:fs/promises')
  const files = await readdir(dir)
  const model = files.find((file) => file.endsWith('.onnx'))
  if (model === undefined) throw new Error(`в голосе ${voice} нет модели .onnx`)
  const runner = piperRunner()
  const id = runner.nextId++
  const file = await new Promise<string>((resolve, reject) => {
    runner.pending.set(id, { resolve, reject })
    runner.child.stdin?.write(`${JSON.stringify({
      id,
      text,
      model: join(dir, model),
      tokens: join(dir, 'tokens.txt'),
      dataDir: join(dir, 'espeak-ng-data'),
    })}\n`)
  })
  try {
    return await readFile(file)
  } finally {
    await rm(file, { force: true }).catch(() => undefined)
  }
}

/** Float32 samples to a PCM16 mono WAV. */
function waveOf(samples: Float32Array, rate: number): Buffer {
  const buffer = Buffer.alloc(44 + samples.length * 2)
  buffer.write('RIFF', 0)
  buffer.writeUInt32LE(36 + samples.length * 2, 4)
  buffer.write('WAVE', 8)
  buffer.write('fmt ', 12)
  buffer.writeUInt32LE(16, 16)
  buffer.writeUInt16LE(1, 20)
  buffer.writeUInt16LE(1, 22)
  buffer.writeUInt32LE(rate, 24)
  buffer.writeUInt32LE(rate * 2, 28)
  buffer.writeUInt16LE(2, 32)
  buffer.writeUInt16LE(16, 34)
  buffer.write('data', 36)
  buffer.writeUInt32LE(samples.length * 2, 40)
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.max(-1, Math.min(1, samples[i]!))
    buffer.writeInt16LE(value < 0 ? value * 0x8000 : value * 0x7fff, 44 + i * 2)
  }
  return buffer
}

/** The voices this computer has, as `say` lists them: name, locale and the sample sentence. */
async function listVoices(): Promise<Array<{ name: string; lang: string }>> {
  const output = await capture('say', ['-v', '?'])
  const voices: Array<{ name: string; lang: string }> = []
  for (const line of output.split('\n')) {
    const match = /^(\S+)\s+([a-z]{2}_[A-Z]{2})\s+#/.exec(line.trim())
    if (match === null) continue
    voices.push({ name: match[1]!, lang: match[2]! })
    if (voices.length >= VOICE_LIMIT) break
  }
  return voices
}

/** Run a process and return its stdout; stderr becomes the error message. */
function capture(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] })
    let stdout = ''
    let stderr = ''
    child.stdout?.on('data', (chunk: Buffer) => {
      stdout += chunk.toString()
    })
    child.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString()
    })
    child.once('error', reject)
    child.once('close', (code) => {
      if (code === 0) resolve(stdout)
      else reject(new Error(stderr.trim() || `${command} exited with ${code ?? 'no code'}`))
    })
  })
}

/** Start a process and wait for it, keeping stderr for the message. */
function run(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'ignore', 'pipe'] })
    let stderr = ''
    child.stderr?.on('data', (chunk: Buffer) => {
      stderr += chunk.toString()
    })
    child.once('error', reject)
    child.once('close', (code) => {
      if (code === 0) resolve()
      else reject(new Error(stderr.trim() || `${command} exited with ${code ?? 'no code'}`))
    })
  })
}

async function readJson(req: IncomingMessage, limit: number): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    const buffer = chunk as Buffer
    size += buffer.length
    if (size > limit) throw new Error('Request body too large')
    chunks.push(buffer)
  }
  if (chunks.length === 0) return {}
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  return parsed !== null && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const json = JSON.stringify(body)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
  res.end(json)
}
