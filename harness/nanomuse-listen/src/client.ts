/**
 * The browser half of continuous listening: a waveform button in the composer.
 *
 * The harness's microphone records one take per click. This one is a mode: while
 * it is on, every phrase separated by a pause is transcribed and sent as a
 * message. Audio is captured with `AudioContext` (no `MediaRecorder`, which
 * cannot be decoded in Electron) and posted to `/nanomuse/voice`, where the
 * local Whisper recognizer turns it into text.
 */
import { createElement as h, useEffect, useRef, useState, type ReactNode } from 'react'

export const name = 'nanomuse-listen'

/** The composer seat the button mounts in. */
export const inject = ['slots']

const RATE = 16000
const ENDPOINT = '/nanomuse/voice'
/** System voices worth trying: the Russian one first, then the notable English ones. */
const VOICES = ['Milena', 'Samantha', 'Zarvox', 'Bubbles', 'Whisper', 'Bells']

/** Recognizers NanoLika Cloud offers; the id is what its /v1/audio/transcriptions expects. */
const CLOUD_STT = [
  { id: 'ear-ru', name: 'GigaAM v3 (русский)' },
  { id: 'ear-turbo', name: 'Whisper Large v3 Turbo' },
  { id: 'ear', name: 'Whisper 1' },
  { id: 'bh-assembly-ai-best', name: 'Assembly AI Best' },
  { id: 'bh-tbank-voicekit-stt', name: 'Voicekit STT (Т-Банк)' },
]

interface Settings {
  voice: string
  answers: boolean
  tts: 'system' | 'silero' | 'cloud'
}

let settingsState: Settings = { voice: VOICES[0]!, answers: false, tts: 'system', ttsModel: 'voice', cloudVoice: 'alloy', piperVoice: 'vits-piper-ru_RU-irina-medium', stt: 'local', sttModel: 'ear-ru', sttFallback: true }
let settingsReady = false
const settingsSubs = new Set<() => void>()
function emitSettings(): void {
  for (const sub of settingsSubs) sub()
}
async function ensureSettings(): Promise<void> {
  if (settingsReady) return
  settingsReady = true
  try {
    const res = await fetch('/nanomuse/listen/settings')
    if (res.ok) {
      const saved = (await res.json()) as Partial<Settings>
      settingsState = {
        voice: typeof saved.voice === 'string' && saved.voice !== '' ? saved.voice : settingsState.voice,
        answers: saved.answers === true,
        tts: saved.tts === 'piper' || saved.tts === 'cloud' ? saved.tts : 'system',
        ttsModel: typeof saved.ttsModel === 'string' && saved.ttsModel !== '' ? saved.ttsModel : settingsState.ttsModel,
        cloudVoice: typeof saved.cloudVoice === 'string' && saved.cloudVoice !== '' ? saved.cloudVoice : settingsState.cloudVoice,
        piperVoice: typeof saved.piperVoice === 'string' && saved.piperVoice !== '' ? saved.piperVoice : settingsState.piperVoice,
        stt: saved.stt === 'cloud' ? 'cloud' : 'local',
        sttModel: typeof saved.sttModel === 'string' && saved.sttModel !== '' ? saved.sttModel : settingsState.sttModel,
        sttFallback: saved.sttFallback !== false,
      }
      if (settingsState.answers) {
        // the microphone is not open after a restart, so nothing should be read aloud either
        settingsState = { ...settingsState, answers: false }
        void fetch('/nanomuse/listen/settings', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ answers: false }),
        }).catch(() => undefined)
      }
      emitSettings()
      if (settingsState.tts === 'piper') warmup()
    }
  } catch {
    // the route is absent in a headless composition; defaults are enough
  }
}
function patchSettings(patch: Partial<Settings>): void {
  settingsState = { ...settingsState, ...patch }
  emitSettings()
  void fetch('/nanomuse/listen/settings', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch),
  }).catch(() => undefined)
}
function useSettings(): Settings {
  const [, force] = useState(0)
  useEffect(() => {
    const sub = () => force((tick) => tick + 1)
    settingsSubs.add(sub)
    void ensureSettings()
    return () => {
      settingsSubs.delete(sub)
    }
  }, [])
  return settingsState
}

interface InputActions {
  captureInsertion(): unknown
  insertText(text: string, span: unknown): boolean
  setDraft(text: string): void
  submit(): void
}

interface Props {
  inputActions?: InputActions
}

const WORDS = {
  ru: { start: 'Постоянное слушание', stop: 'Остановить постоянное слушание' },
  en: { start: 'Continuous listening', stop: 'Stop continuous listening' },
  zh: { start: '持续聆听', stop: '停止持续聆听' },
}

function words(): { start: string; stop: string } {
  const lang = (document.documentElement.lang || navigator.language || 'en').slice(0, 2)
  return lang === 'ru' ? WORDS.ru : lang === 'zh' ? WORDS.zh : WORDS.en
}

function encodeWave(samples: Float32Array): Uint8Array {
  const bytes = new Uint8Array(44 + samples.length * 2)
  const view = new DataView(bytes.buffer)
  const tag = (at: number, text: string) => {
    for (let i = 0; i < 4; i += 1) view.setUint8(at + i, text.charCodeAt(i))
  }
  tag(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  tag(8, 'WAVE')
  tag(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, RATE, true)
  view.setUint32(28, RATE * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  tag(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]!))
    view.setInt16(44 + i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true)
  }
  return bytes
}

function resample(samples: Float32Array, from: number): Float32Array {
  if (from === RATE) return samples
  const length = Math.max(1, Math.round((samples.length * RATE) / from))
  const out = new Float32Array(length)
  const step = from / RATE
  for (let i = 0; i < length; i += 1) {
    const at = i * step
    const left = Math.floor(at)
    const right = Math.min(samples.length - 1, left + 1)
    const weight = at - left
    out[i] = (samples[left] ?? 0) * (1 - weight) + (samples[right] ?? 0) * weight
  }
  return out
}

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]!)
  return btoa(binary)
}

function makeSplitter(onUtterance: (samples: Float32Array) => void, settings: { silenceMs: number; minMs: number; maxMs?: number; onVoiceStart?: () => void }) {
  const parts: Float32Array[] = []
  let heard = 0
  let silence = 0
  let voicing = false
  let rate = 0
  // the room's own noise, learned while nobody speaks: a television or a fan raises the bar
  let floor = 0.006
  let muted = false
  const collect = (): Float32Array => {
    const total = parts.reduce((sum, part) => sum + part.length, 0)
    const all = new Float32Array(total)
    let at = 0
    for (const part of parts) {
      all.set(part, at)
      at += part.length
    }
    return all
  }
  const reset = () => {
    parts.length = 0
    heard = 0
    silence = 0
    voicing = false
  }
  return {
    /** Deaf for a while: the answer is playing, and a microphone that hears it answers itself. */
    mute(on: boolean): void {
      muted = on
      if (on) reset()
    },
    push(samples: Float32Array, sampleRate: number): void {
      if (muted) return
      rate = sampleRate
      let sum = 0
      for (let i = 0; i < samples.length; i += 1) sum += samples[i]! * samples[i]!
      const rms = Math.sqrt(sum / samples.length)
      // The bar follows the room: a television that is always on lifts the floor instead of
      // being taken for speech in every phrase.
      if (!voicing) floor = floor * 0.97 + rms * 0.03
      const voice = rms > Math.max(0.012, floor * 3)
      if (voice) {
        if (!voicing) settings.onVoiceStart?.()
        voicing = true
        silence = 0
        parts.push(Float32Array.from(samples))
        heard += samples.length
        if ((heard / sampleRate) * 1000 >= (settings.maxMs ?? 30_000)) {
          onUtterance(collect())
          reset()
        }
        return
      }
      if (!voicing) return
      silence += (samples.length / sampleRate) * 1000
      if (silence < settings.silenceMs) return
      if ((heard / rate) * 1000 >= settings.minMs) onUtterance(collect())
      reset()
    },
    flush(): void {
      if (voicing && heard > 0) onUtterance(collect())
      reset()
    },
  }
}

function VoiceListen(props: Props): ReactNode {
  const settings = useSettings()
  const [state, setState] = useState<'idle' | 'listening' | 'working'>('idle')
  const [failure, setFailure] = useState('')
  const [analysis, setAnalysis] = useState('')
  const [voiceTick, setVoiceTick] = useState(0)
  const run = useRef<{ stop(): void } | null>(null)
  const text = words()

  const deliver = (phrase: string) => {
    const actions = props.inputActions
    if (actions === undefined) {
      // No composer seat: leave a trace for the person reading the console.
      console.warn('nanomuse-listen: the composer did not hand inputActions; phrase not sent:', phrase)
      return
    }
    actions.setDraft(phrase)
    // the draft is React state: submit in the next tick reads the phrase, not the empty editor
    setTimeout(() => actions.submit(), 80)
  }
  // отладочный хук: из консоли можно проверить отправку без микрофона
  ;(globalThis as unknown as { __listenDeliver?: (text: string) => void }).__listenDeliver = deliver

  let holdUntil = 0
  const send = (samples: Float32Array, rate: number) => {
    const wave = encodeWave(resample(samples, rate))
    const seconds = samples.length / rate
    let sum = 0
    for (let i = 0; i < samples.length; i += 1) sum += samples[i]! * samples[i]!
    const rms = Math.sqrt(sum / Math.max(1, samples.length))
    setState('working')
    void fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ audio: toBase64(wave), language: 'ru' }),
    })
      .then(async (res) => {
        const answer = (await res.json().catch(() => ({}))) as { text?: string; error?: { message?: string } }
        if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`)
        const phrase = (answer.text ?? '').trim()
        if (phrase !== '') {
          deliver(phrase)
          const words = phrase.split(/\s+/).filter(Boolean).length
          const wpm = seconds > 0.5 ? Math.round(words / (seconds / 60)) : 0
          const loud = rms > 0.06 ? 'громко' : rms > 0.025 ? 'обычно' : 'тихо'
          setAnalysis(`${seconds.toFixed(1)} с · ${wpm} слов/мин · ${loud}`)
        }
        setFailure('')
        if (run.current !== null) setState('listening')
      })
      .catch((error: unknown) => {
        setFailure(error instanceof Error ? error.message : 'Распознавание не отвечает')
        if (run.current !== null) setState('listening')
      })
  }

  const start = async () => {
    try {
      // a live conversation answers out loud: the two switches are one intent
      patchSettings({ answers: true })
      warmup()
      // Echo cancellation and noise suppression are the browser's own: without them the
      // answer comes back through the speakers and is heard as a new question.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      const context = new AudioContext()
      if (context.state !== 'running') await context.resume().catch(() => undefined)
      const source = context.createMediaStreamSource(stream)
      const processor = context.createScriptProcessor(4096, 1, 1)
      const splitter = makeSplitter((samples) => send(samples, context.sampleRate), {
          silenceMs: 900,
          minMs: 350,
          onVoiceStart: () => {
            // Only the spoken answer steps aside. The turn itself is never cancelled from
            // here: a cough, a door or the echo of our own voice used to kill the run in
            // the middle of its tools.
            if (onAir !== null) stopSpeaking()
          },
        })
      // The host also speaks on its own (the `speak` tool, the platform's `say`): it says so
      // on its route, and the microphone stays deaf while those words are in the room.
      let hostSpeaking = false
      const askHost = setInterval(() => {
        void fetch('/nanomuse/cloud/speaking')
          .then(async (res) => {
            if (!res.ok) return
            const answer = (await res.json().catch(() => ({}))) as { speaking?: boolean }
            hostSpeaking = answer.speaking === true
          })
          .catch(() => undefined)
      }, 700)
      processor.onaudioprocess = (event) => {
        splitter.mute(onAir !== null || hostSpeaking)
        splitter.push(event.inputBuffer.getChannelData(0), context.sampleRate)
      }
      source.connect(processor)
      processor.connect(context.destination)
      let watchdog: ReturnType<typeof setInterval> | undefined
      const finish = () => {
        patchSettings({ answers: false })
        if (watchdog !== undefined) clearInterval(watchdog)
        clearInterval(askHost)
        processor.disconnect()
        source.disconnect()
        splitter.flush()
        stream.getTracks().forEach((track) => track.stop())
        void context.close().catch(() => undefined)
        run.current = null
        setState('idle')
        setFailure('')
      }
      const track = stream.getAudioTracks()[0]
      watchdog = setInterval(() => {
        if (context.state !== 'running') void context.resume().catch(() => undefined)
        if (track !== undefined && track.readyState === 'ended') finish()
      }, 1500)
      run.current = { stop: finish }
      setState('listening')
    } catch (error) {
      setFailure(error instanceof Error ? error.message : 'Микрофон недоступен')
      setState('idle')
    }
  }

  const active = state !== 'idle'
  return h('button', {
    type: 'button',
    className: `nm-listen${active ? ' nm-listen-live' : ''}${failure !== '' ? ' nm-listen-failed' : ''}`,
    'aria-label': active ? text.stop : text.start,
    title: failure !== '' ? failure : active ? text.stop : text.start,
    'aria-pressed': active,
    onMouseDown: (event: { preventDefault(): void }) => event.preventDefault(),
    onContextMenu: (event: { preventDefault(): void }) => {
      event.preventDefault()
      const now = settings.voice
      const next = VOICES[(VOICES.indexOf(now) + 1) % VOICES.length]!
      patchSettings({ voice: next })
      setAnalysis('')
      setVoiceTick((tick) => tick + 1)
    },
    onClick: () => {
      if (state !== 'idle') run.current?.stop()
      else void start()
    },
  }, h('svg', { width: 17, height: 17, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.75, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
    h('path', { d: 'M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4' })))
}

const STYLE = `
.nm-listen { width: 34px; height: 34px; border: 0; padding: 0; border-radius: 50%; background: transparent; color: var(--dsw-alias-label-secondary); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.nm-listen:hover { background: var(--nm-hover, rgba(0,0,0,0.06)); color: var(--dsw-alias-label-primary); }
.nm-listen.nm-listen-live { color: #fff; background: #0f9d8f; box-shadow: 0 0 0 4px rgba(15,157,143,0.25); animation: nm-listen-pulse 4.8s ease-in-out infinite; }
.nm-listen.nm-listen-failed { color: #fff; background: #b3261e; }
.nm-speak { display: none !important; width: 34px; height: 34px; border: 0; padding: 0; border-radius: 50%; background: transparent; color: var(--dsw-alias-label-secondary); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.nm-speak:hover { background: var(--nm-hover, rgba(0,0,0,0.06)); color: var(--dsw-alias-label-primary); }
.nm-speak.nm-speak-on { color: #fff; background: #0f9d8f; }
html[data-nanomuse] [data-slot="conversation.input.right"] > .nm-listen { order: 1; }
html[data-nanomuse] [data-slot="conversation.input.right"] > .nm-speak { order: 2; }
@keyframes nm-listen-pulse { 0%, 100% { opacity: 0.72; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .nm-listen.nm-listen-live { animation: none; opacity: 1; } }
.nml-settings { display: flex; flex-direction: column; gap: 12px; max-width: 640px; }
.nml-head { margin: 10px 0 0; font-size: 12px; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase; opacity: 0.6; }
.nml-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 32px; }
.nml-title { font-weight: 600; }
.nml-hint { margin: 0; opacity: 0.72; font-size: 13px; line-height: 1.45; }
.nml-heard { margin: 0; padding: 10px 12px; border-radius: 8px; background: var(--nm-hover, rgba(0,0,0,0.05)); }
.nml-select { appearance: none; -webkit-appearance: none; min-width: 220px; padding: 8px 34px 8px 12px; border-radius: 10px; border: 1px solid var(--dsw-alias-border-l2, rgba(0,0,0,0.18)); background-color: var(--dsw-alias-bg-layer-1, #fff); background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%237a7a7a' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 12px center; color: inherit; font: inherit; font-size: 13px; cursor: pointer; }
.nml-select:hover { border-color: var(--dsw-alias-border-l3, rgba(0,0,0,0.3)); }
.nml-select:focus { outline: 2px solid var(--nm-blue, #0064d4); outline-offset: 1px; }
.nml-meter { height: 6px; border-radius: 999px; background: var(--dsw-alias-bg-layer-3, rgba(0,0,0,0.08)); overflow: hidden; margin: -4px 0 4px; }
.nml-meter-fill { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #2f9e5f, #e0a800 70%, #d2453d); transition: width 90ms linear; }
.nml-btn { padding: 6px 12px; border-radius: 999px; border: 1px solid var(--dsw-alias-border, rgba(0,0,0,0.2)); background: transparent; color: inherit; cursor: pointer; }
.nml-btn:hover { background: var(--nm-hover, rgba(0,0,0,0.06)); }
.nml-btn:disabled { opacity: 0.5; cursor: default; }
.nml-actions { display: inline-flex; gap: 8px; }
.nml-btn-stop { border-color: #b3261e; color: #b3261e; }
.nm-chat-cloud-ctl { position: relative; display: inline-flex; }
.nm-cloud-btn { color: var(--dsw-alias-label-secondary); }
.nm-cloud-btn.nm-cloud-on { color: var(--nm-blue, #0064d4); }
.nm-cloud-menu { position: absolute; z-index: 20; top: calc(100% + 6px); right: 0; min-width: 230px; padding: 8px; border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); box-shadow: 0 10px 30px rgba(0,0,0,0.18); border: 1px solid var(--dsw-alias-border-l2, rgba(0,0,0,0.12)); display: flex; flex-direction: column; gap: 4px; }
.nm-cloud-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px 10px; border: 0; border-radius: 8px; background: transparent; color: inherit; font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.nm-cloud-item:hover:not(:disabled) { background: var(--nm-hover, rgba(0,0,0,0.06)); }
.nm-cloud-item:disabled { opacity: 0.45; cursor: default; }
.nm-cloud-note { padding: 4px 10px 2px; font-size: 12px; opacity: 0.7; }
`

let styled = false
function ensureStyle(): void {
  if (styled) return
  styled = true
  const style = document.createElement('style')
  style.id = 'nanomuse-listen-style'
  style.textContent = STYLE
  document.head.append(style)
}

/** Ask the host to load the local voice now, so the first sentence is not the slow one. */
function warmup(): void {
  void fetch('/nanomuse/listen/warmup', { method: 'POST' }).catch(() => undefined)
}

/** The shell's own settings navigation, handed to us by its `settings.onboarding` seat. */
let openSection: ((id: string) => void) | null = null

/** Open the account section through the shell, the way its own sections do. */
function openCloudSettings(): void {
  openSection?.('nanomuse-cloud')
}

/**
 * Take the shell's `openSection` and keep it: the account screen belongs to the
 * bundle, and the only supported way to show it is this seat.
 */
function SettingsBridge(props: { openSection?: (id: string) => void }): ReactNode {
  useEffect(() => {
    if (typeof props.openSection === 'function') openSection = props.openSection
    return () => {
      openSection = null
    }
  }, [props.openSection])
  return null
}

/** The system's own dictation shortcut, in the words this platform uses. */
function dictationHint(): string {
  const platform = navigator.platform.toLowerCase()
  if (platform.includes('mac')) return 'Нажмите клавишу диктовки (или Fn дважды), когда курсор стоит в поле ввода: система напечатает сказанное.'
  if (platform.includes('win')) return 'Нажмите Win+H, когда курсор стоит в поле ввода: Windows напечатает сказанное.'
  return 'Включите диктовку своей системы, когда курсор стоит в поле ввода.'
}

/** Record a fixed take and return its samples; used by the microphone test. */
async function recordTake(seconds: number, onLevel?: (level: number) => void): Promise<{ samples: Float32Array; rate: number }> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const context = new AudioContext()
  if (context.state !== 'running') await context.resume().catch(() => undefined)
  const source = context.createMediaStreamSource(stream)
  const processor = context.createScriptProcessor(4096, 1, 1)
  const parts: Float32Array[] = []
  processor.onaudioprocess = (event) => {
    const chunk = event.inputBuffer.getChannelData(0)
    parts.push(Float32Array.from(chunk))
    if (onLevel !== undefined) {
      let sum = 0
      for (let i = 0; i < chunk.length; i += 1) sum += chunk[i]! * chunk[i]!
      onLevel(Math.min(1, Math.sqrt(sum / chunk.length) * 6))
    }
  }
  source.connect(processor)
  processor.connect(context.destination)
  await new Promise((resolve) => setTimeout(resolve, seconds * 1000))
  processor.disconnect()
  source.disconnect()
  stream.getTracks().forEach((track) => track.stop())
  const rate = context.sampleRate
  void context.close().catch(() => undefined)
  const total = parts.reduce((sum, part) => sum + part.length, 0)
  const samples = new Float32Array(total)
  let at = 0
  for (const part of parts) {
    samples.set(part, at)
    at += part.length
  }
  return { samples, rate }
}

/** Play one rendered answer; resolves when it ends or fails. */
function playAudio(audio: string): Promise<void> {
  return new Promise((resolve) => {
    const track = new Audio(`data:audio/wav;base64,${audio}`)
    track.onended = () => resolve()
    track.onerror = () => resolve()
    track.play().catch(() => resolve())
  })
}

interface SttProvider {
  id: string
  name: string
  location: string
  languages: readonly string[]
  preparation: { phase: string }
}

interface SttSnapshot {
  providers: SttProvider[]
  selection: { providerId: string; language: string }
}

function DictationSettings(): ReactNode {
  const settings = useSettings()
  const [voices, setVoices] = useState<Array<{ name: string; lang: string }>>([])
  const [status, setStatus] = useState('')
  const [heard, setHeard] = useState('')
  const [busy, setBusy] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [level, setLevel] = useState(0)
  const [stt, setStt] = useState<SttSnapshot | null>(null)
  const [ttsProviders, setTtsProviders] = useState<Array<{ id: string; name: string; ready: boolean }>>([])
  const [health, setHealth] = useState<{ cloud: { ok: boolean; latencyMs: number; error: string }; stt: string } | null>(null)
  const [piperVoices, setPiperVoices] = useState<Array<{ id: string; title: string; lang: string; installed: boolean }>>([])

  const loadStt = () => {
    void fetch('/nanomuse/listen/stt')
      .then(async (res) => (res.ok ? ((await res.json()) as SttSnapshot) : null))
      .then((snapshot) => {
        if (snapshot !== null) setStt(snapshot)
      })
      .catch(() => undefined)
  }

  useEffect(loadStt, [])
  useEffect(() => {
    const load = () => {
      void fetch('/nanomuse/listen/health')
        .then(async (res) => (res.ok ? ((await res.json()) as { cloud: { ok: boolean; latencyMs: number; error: string }; stt: string }) : null))
        .then((answer) => {
          if (answer !== null) setHealth(answer)
        })
        .catch(() => undefined)
    }
    load()
    const timer = setInterval(load, 30_000)
    return () => clearInterval(timer)
  }, [])
  const loadPiper = () => {
    void fetch('/nanomuse/listen/tts/voices')
      .then(async (res) => (res.ok ? ((await res.json()) as { voices?: Array<{ id: string; title: string; lang: string; installed: boolean }> }) : {}))
      .then((answer) => setPiperVoices(Array.isArray(answer.voices) ? answer.voices : []))
      .catch(() => undefined)
  }
  useEffect(loadPiper, [])

  const installPiper = async (id: string) => {
    setBusy(true)
    setStatus('Скачиваю голос…')
    try {
      const res = await fetch('/nanomuse/listen/tts/install', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id }),
      })
      if (!res.ok) {
        const answer = (await res.json().catch(() => ({}))) as { error?: { message?: string } }
        throw new Error(answer.error?.message ?? `${res.status}`)
      }
      loadPiper()
      patchSettings({ piperVoice: id })
      setStatus('Голос скачан')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void fetch('/nanomuse/listen/tts')
      .then(async (res) => (res.ok ? ((await res.json()) as { providers?: Array<{ id: string; name: string; ready: boolean }> }) : {}))
      .then((answer) => setTtsProviders(Array.isArray(answer.providers) ? answer.providers : []))
      .catch(() => undefined)
  }, [])

  const chooseStt = async (providerId: string) => {
    setBusy(true)
    try {
      const res = await fetch('/nanomuse/listen/stt', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ providerId }),
      })
      const answer = (await res.json().catch(() => ({}))) as SttSnapshot & { error?: { message?: string } }
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`)
      if (Array.isArray(answer.providers)) setStt(answer)
      setStatus('Распознавание переключено')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setBusy(false)
    }
  }

  const chooseLanguage = async (language: string) => {
    setBusy(true)
    try {
      const res = await fetch('/nanomuse/listen/stt', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ language }),
      })
      const answer = (await res.json().catch(() => ({}))) as SttSnapshot & { error?: { message?: string } }
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`)
      if (Array.isArray(answer.providers)) setStt(answer)
    } catch {
      // the picker keeps the previous language
    } finally {
      setBusy(false)
    }
  }

  const prepareStt = async (providerId: string) => {
    setBusy(true)
    try {
      const res = await fetch('/nanomuse/listen/stt/prepare', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ providerId }),
      })
      if (!res.ok) {
        const answer = (await res.json().catch(() => ({}))) as { error?: { message?: string } }
        throw new Error(answer.error?.message ?? `${res.status}`)
      }
      setStatus('Модель готовится…')
      setTimeout(loadStt, 3000)
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setBusy(false)
    }
  }
  const testVoice = async () => {
    setBusy(true)
    setStatus('Готовлю голос…')
    try {
      const res = await fetch('/nanomuse/speak', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text: 'Привет, Денис. Так звучит выбранный голос. Если слышно — всё настроено.', voice: settings.voice, provider: settings.tts }),
      })
      const answer = (await res.json()) as { audio?: string; error?: { message?: string } }
      if (!res.ok || answer.audio === undefined) throw new Error(answer.error?.message ?? `${res.status}`)
      setStatus('Играю…')
      setPlaying(true)
      await playAudio(answer.audio)
      setStatus('Голос проверен')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setPlaying(false)
      setBusy(false)
    }
  }

  const testMic = async () => {
    setBusy(true)
    setHeard('')
    setStatus('Говори 3 секунды…')
    try {
      const { samples, rate } = await recordTake(3, (value) => setLevel(value))
      setLevel(0)
      setStatus('Распознаю…')
      const wave = encodeWave(resample(samples, rate))
      const res = await fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ audio: toBase64(wave), language: 'ru' }),
      })
      const answer = (await res.json()) as { text?: string; error?: { message?: string } }
      if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`)
      const phrase = (answer.text ?? '').trim()
      setHeard(phrase === '' ? '(тишина, речь не разобрана)' : phrase)
      setStatus('Микрофон проверен')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setBusy(false)
    }
  }

  const listed = voices.length > 0 ? voices.map((voice) => voice.name) : VOICES
  const voiceOptions = listed.includes(settings.voice) ? listed : [settings.voice, ...listed]
  const provider = stt?.providers?.find((item) => item.id === stt.selection?.providerId)
  const languages = provider?.languages ?? ['ru']
  const phase = provider?.preparation?.phase ?? 'unprepared'
  const phaseWord = phase === 'ready' ? 'готова' : phase === 'downloading' ? 'скачивается' : phase === 'loading' ? 'загружается' : phase === 'failed' ? 'ошибка' : 'не подготовлена'
  const ttsWord = settings.tts === 'system' ? 'Системный голос' : settings.tts === 'silero' ? 'Silero (локально, не установлен)' : 'Облако (не настроено)'
  return h('div', { className: 'nml-settings' },
    h('p', { className: 'nml-hint' }, 'Детекция речи и голос Лики. Всё работает на этом компьютере: распознаёт локальная нейросеть, говорит системный голос.'),

    h('h3', { className: 'nml-head' }, 'Детекция речи'),
    h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Распознаватель'),
      h('select', {
        className: 'nml-select',
        value: settings.stt === 'cloud' ? 'cloud' : (stt?.selection?.providerId ?? ''),
        onChange: (event: { target: { value: string } }) => {
          const value = event.target.value
          if (value === 'cloud') patchSettings({ stt: 'cloud' })
          else {
            patchSettings({ stt: 'local' })
            void chooseStt(value)
          }
        },
      }, [
        ...(stt?.providers ?? []).map((item) => h('option', { key: item.id, value: item.id },
          `${item.name}${item.location === 'cloud' ? ' (облако)' : ' (локально)'}${item.id === 'whisper-local' ? ' — по умолчанию' : ''}`)),
        h('option', { key: 'cloud', value: 'cloud' }, 'NanoLika Cloud (облако)'),
      ])),
    settings.stt === 'cloud' ? h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Модель облака'),
      h('select', {
        className: 'nml-select',
        value: settings.sttModel,
        onChange: (event: { target: { value: string } }) => patchSettings({ sttModel: event.target.value }),
      }, CLOUD_STT.map((model) => h('option', { key: model.id, value: model.id }, `${model.name}${model.id === 'ear-ru' ? ' — по умолчанию' : ''}`)))) : null,
    h('div', { className: 'nml-row' },
      h('span', { className: 'nml-hint' }, health === null
        ? 'Проверяю облако…'
        : health.cloud.ok
          ? `Облако доступно · ${health.cloud.latencyMs} мс · отвечает: ${health.stt === 'local' ? 'локально' : health.stt}`
          : `Облако недоступно: ${health.cloud.error}. Работаем локально, переключение автоматическое.`),
      health !== null && !health.cloud.ok
        ? h('button', { type: 'button', className: 'nml-btn', onClick: () => openCloudSettings() }, 'Войти заново')
        : null),
    settings.stt === 'cloud' ? h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'При сбое облака — локально'),
      h('input', {
        type: 'checkbox',
        checked: settings.sttFallback,
        onChange: (event: { target: { checked: boolean } }) => patchSettings({ sttFallback: event.target.checked }),
      })) : null,
    settings.stt === 'cloud' ? null : h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Язык'),
      h('select', {
        className: 'nml-select',
        value: stt?.selection?.language ?? 'ru',
        onChange: (event: { target: { value: string } }) => void chooseLanguage(event.target.value),
      }, languages.map((code) => h('option', { key: code, value: code }, code === 'auto' ? 'определять сам' : code)))),
    h('div', { className: 'nml-row' },
      h('span', { className: 'nml-hint' }, `Модель: ${phaseWord}`),
      h('button', { type: 'button', className: 'nml-btn', disabled: busy || phase === 'ready', onClick: () => provider !== undefined && void prepareStt(provider.id) }, 'Подготовить модель')),
    h('div', { className: 'nml-row' },
      h('span', { className: 'nml-hint' }, 'Запись 3 секунды и распознавание'),
      h('button', { type: 'button', className: 'nml-btn', disabled: busy, onClick: () => void testMic() }, 'Проверить микрофон')),
    h('div', { className: 'nml-meter', role: 'meter', 'aria-label': 'Уровень микрофона', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(level * 100) },
      h('span', { className: 'nml-meter-fill', style: { width: `${Math.round(level * 100)}%` } })),
    heard !== '' ? h('p', { className: 'nml-heard' }, `Услышала: ${heard}`) : null,

    h('h3', { className: 'nml-head' }, 'Генерация голоса'),
    h('p', { className: 'nml-hint' }, 'Движок — кто озвучивает (система, Piper или облако). Голос — кто именно говорит внутри выбранного движка.'),
    h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Движок озвучки'),
      h('select', {
        className: 'nml-select',
        value: settings.tts,
        onChange: (event: { target: { value: string } }) => patchSettings({ tts: event.target.value as Settings['tts'] }),
      }, (ttsProviders.length > 0 ? ttsProviders : [
        { id: 'system', name: 'Системный голос', ready: true },
        { id: 'piper', name: 'Piper Ирина (нейроголос, локально)', ready: true },
        { id: 'cloud', name: 'NanoLika Cloud (облако)', ready: true },
      ]).map((item) => h('option', { key: item.id, value: item.id },
        `${item.name}${item.id === 'system' ? ' — по умолчанию' : ''}`)))),
    settings.tts === 'system' ? h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Голос'),
      h('select', {
        className: 'nml-select',
        value: settings.voice,
        onChange: (event: { target: { value: string } }) => patchSettings({ voice: event.target.value }),
      }, voiceOptions.map((name) => h('option', { key: name, value: name }, name)))) : null,
    settings.tts === 'piper' ? h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Голос'),
      h('select', {
        className: 'nml-select',
        value: settings.piperVoice,
        onChange: (event: { target: { value: string } }) => patchSettings({ piperVoice: event.target.value }),
      }, (piperVoices.length > 0 ? piperVoices : [{ id: 'vits-piper-ru_RU-irina-medium', title: 'Ирина — русский, женский', lang: 'ru', installed: true }]).map((voice) =>
        h('option', { key: voice.id, value: voice.id }, `${voice.title}${voice.installed ? '' : ' — не скачан'}`)))) : null,
    settings.tts === 'piper' && piperVoices.some((voice) => voice.id === settings.piperVoice && !voice.installed) ? h('div', { className: 'nml-row' },
      h('span', { className: 'nml-hint' }, 'Голос ещё не скачан на этот компьютер'),
      h('button', { type: 'button', className: 'nml-btn', disabled: busy, onClick: () => void installPiper(settings.piperVoice) }, 'Скачать голос')) : null,
    settings.tts === 'cloud' ? h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Модель облака'),
      h('select', {
        className: 'nml-select',
        value: settings.ttsModel,
        onChange: (event: { target: { value: string } }) => patchSettings({ ttsModel: event.target.value }),
      },
        h('option', { value: 'voice' }, 'Gemini Voice — по умолчанию'),
        h('option', { value: 'voice-hd' }, 'MiniMax Speech HD'),
        h('option', { value: 'bh-tts-1' }, 'TTS 1'),
        h('option', { value: 'bh-tts-1-hd' }, 'TTS 1 HD'))) : null,
    settings.tts === 'cloud' ? h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Голос облака'),
      h('select', {
        className: 'nml-select',
        value: settings.cloudVoice,
        onChange: (event: { target: { value: string } }) => patchSettings({ cloudVoice: event.target.value }),
      }, ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'].map((name) => h('option', { key: name, value: name }, name)))) : null,
    h('div', { className: 'nml-row' },
      h('span', { className: 'nml-hint' }, status !== '' ? status : ttsWord),
      h('span', { className: 'nml-actions' },
        h('button', { type: 'button', className: 'nml-btn', disabled: busy && !playing, onClick: () => void testVoice() }, 'Проверить голос'),
        playing ? h('button', { type: 'button', className: 'nml-btn nml-btn-stop', onClick: () => { stopSpeaking(); setPlaying(false); setStatus('Остановлено') } }, 'Стоп') : null)),
    h('label', { className: 'nml-row' },
      h('span', { className: 'nml-title' }, 'Озвучивать ответы'),
      h('input', {
        type: 'checkbox',
        checked: settings.answers,
        onChange: (event: { target: { checked: boolean } }) => patchSettings({ answers: event.target.checked }),
      })),

    h('h3', { className: 'nml-head' }, 'Системная диктовка'),
    h('p', { className: 'nml-hint' }, dictationHint()),
  )
}


/** The chat's cloud switch and the ways into the paired messengers, in the header. */
function ChatCloudButton(props: { sessionId?: string }): ReactNode {
  const sessionId = props.sessionId ?? ''
  const [synced, setSynced] = useState(false)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [channels, setChannels] = useState<Array<{ channel: string }>>([])

  useEffect(() => {
    if (sessionId === '') return
    void fetch('/nanomuse/cloud/sync/state', { headers: { 'x-nanomuse': '1' } })
      .then(async (res) => (res.ok ? ((await res.json()) as { synced?: string[] }) : {}))
      .then((view) => setSynced(Array.isArray(view.synced) && view.synced.includes(sessionId)))
      .catch(() => undefined)
  }, [sessionId])

  useEffect(() => {
    void fetch('/nanomuse/cloud/link/channels', { headers: { 'x-nanomuse': '1' } })
      .then(async (res) => (res.ok ? ((await res.json()) as { channels?: Array<{ channel: string }> }) : {}))
      .then((answer) => setChannels(Array.isArray(answer.channels) ? answer.channels : []))
      .catch(() => undefined)
  }, [])

  if (sessionId === '') return null

  const toggle = async (on: boolean) => {
    setBusy(true)
    setNote('')
    try {
      const res = await fetch('/nanomuse/cloud/sync/synced', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-nanomuse': '1' },
        body: JSON.stringify({ sessionId, on }),
      })
      const answer = (await res.json().catch(() => ({}))) as { ok?: boolean; reason?: string; error?: { message?: string } }
      if (!res.ok || answer.ok === false) throw new Error(answer.error?.message ?? answer.reason ?? `${res.status}`)
      setSynced(on)
      setNote(on ? 'Чат в облаке' : 'Только на этом компьютере')
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setBusy(false)
    }
  }

  const openIn = async (channel: 'telegram' | 'max') => {
    setBusy(true)
    setNote('')
    try {
      const res = await fetch('/nanomuse/cloud/conversation/open', {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-nanomuse': '1' },
        body: JSON.stringify({ sessionId, channel }),
      })
      if (!res.ok) {
        const answer = (await res.json().catch(() => ({}))) as { error?: { message?: string } }
        throw new Error(answer.error?.message ?? `${res.status}`)
      }
      setNote(channel === 'telegram' ? 'Открыл в Telegram' : 'Открыл в MAX')
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Не получилось')
    } finally {
      setBusy(false)
    }
  }

  const hasTelegram = channels.some((item) => item.channel === 'telegram')
  const hasMax = channels.some((item) => item.channel === 'max')
  const title = synced ? 'В облаке: веб и устройства' : 'Только на этом компьютере'
  return h('div', { className: 'nm-chat-cloud-ctl' },
    h('button', {
      type: 'button',
      className: `nm-icon-btn nm-cloud-btn${synced ? ' nm-cloud-on' : ''}`,
      'aria-label': title,
      title,
      disabled: busy,
      onClick: () => setOpen((current) => !current),
    }, h('svg', { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
      h('path', { d: 'M2.25 15a4.5 4.5 0 0 0 4.5 4.5H18a3.75 3.75 0 0 0 1.332-7.257 3 3 0 0 0-3.758-3.848 5.25 5.25 0 0 0-10.233 2.33A4.502 4.502 0 0 0 2.25 15z' }),
      synced ? null : h('path', { d: 'M3.5 3.5l17 17' }))),
    open ? h('div', { className: 'nm-cloud-menu' },
      h('label', { className: 'nm-cloud-item' },
        h('input', { type: 'checkbox', checked: synced, disabled: busy, onChange: (event: { target: { checked: boolean } }) => void toggle(event.target.checked) }),
        h('span', null, 'Облако: веб и все устройства')),
      h('button', { type: 'button', className: 'nm-cloud-item', disabled: busy || !hasTelegram, onClick: () => void openIn('telegram') }, 'Открыть в Telegram'),
      h('button', { type: 'button', className: 'nm-cloud-item', disabled: busy || !hasMax, onClick: () => void openIn('max') }, 'Открыть в MAX'),
      note !== '' ? h('span', { className: 'nm-cloud-note' }, note) : null) : null)
}

interface SlotsLike {
  inject(name: string, callback: () => unknown): unknown
  register(options: Record<string, unknown>, component: unknown): unknown
}

/** The answer track currently on air; a new phrase stops it (barge-in). */
let onAir: HTMLAudioElement | null = null
let dropQueue: (() => void) | null = null

/** Silence the answer and forget what was queued behind it. */
function stopSpeaking(): void {
  dropQueue?.()
  if (onAir !== null) {
    onAir.pause()
    onAir = null
  }
}


function VoiceAnswers(): ReactNode {
  const settings = useSettings()
  const on = settings.answers
  const queue = useRef<string[]>([])
  const playing = useRef(false)
  const seen = useRef(new WeakSet<Element>())
  const timers = useRef(new Map<Element, ReturnType<typeof setInterval>>())
  const spoken = useRef(new Map<Element, string>())
  const lastChange = useRef(new Map<Element, number>())
  const ru = (document.documentElement.lang || '').startsWith('ru')

  useEffect(() => {
    dropQueue = () => {
      queue.current.length = 0
      playing.current = false
    }
    return () => {
      dropQueue = null
    }
  }, [])

  const playNext = () => {
    if (playing.current) return
    const next = queue.current.shift()
    if (next === undefined) return
    playing.current = true
    void fetch('/nanomuse/speak', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text: next, voice: settings.voice, provider: settings.tts }),
    })
      .then(async (res) => {
        const answer = (await res.json().catch(() => ({}))) as { audio?: string; error?: { message?: string } }
        if (!res.ok) throw new Error(answer.error?.message ?? `${res.status}`)
        const track = new Audio(`data:audio/wav;base64,${answer.audio ?? ''}`)
        onAir = track
        track.onended = () => {
          if (onAir === track) onAir = null
          playing.current = false
          playNext()
        }
        track.onerror = () => {
          if (onAir === track) onAir = null
          playing.current = false
          playNext()
        }
        await track.play()
      })
      .catch(() => {
        playing.current = false
        playNext()
      })
  }

  const speak = (text: string) => {
    const plain = text.trim()
    if (plain === '') return
    queue.current.push(plain)
    playNext()
  }

  useEffect(() => {
    if (!on) return
    // Only the answer itself: reasoning and tool steps live in their own assistant-step
    // nodes with data-chat-group-part="reasoning".
    const RESPONSE = '[data-chat-flow-kind=assistant-step][data-chat-group-part=response]'
    const watch = (node: Element, quietly = false) => {
      if (seen.current.has(node)) return
      seen.current.add(node)
      if (quietly) return
      const clean = () => (node.textContent ?? '').replace(/\s+/g, ' ').trim()
      const pump = () => {
        const text = clean()
        if (text === '') return
        let done = spoken.current.get(node) ?? ''
        if (!text.startsWith(done)) done = ''
        const fresh = text.slice(done.length)
        const boundary = Math.max(fresh.lastIndexOf('. '), fresh.lastIndexOf('! '), fresh.lastIndexOf('? '), fresh.lastIndexOf('… '))
        const atEnd = /[.!?…]\s*$/.test(fresh)
        // the very first piece may be cut at a word boundary: waiting for a full stop
        // costs the person a second of silence at the start of every answer
        const early = done === '' && fresh.length >= 48
        const space = early ? Math.max(fresh.lastIndexOf(', '), fresh.lastIndexOf(' ')) : -1
        const cut = boundary > 0 ? boundary + 1 : atEnd ? fresh.length : space > 0 ? space + 1 : -1
        if (cut > 0) {
          const piece = fresh.slice(0, cut).trim()
          if (piece !== '') speak(piece)
          spoken.current.set(node, text.slice(0, done.length + cut))
        }
      }
      const timer = setInterval(() => {
        pump()
        const last = lastChange.current.get(node) ?? Date.now()
        if (Date.now() - last > 600) {
          const text = clean()
          const done = spoken.current.get(node) ?? ''
          const rest = (text.startsWith(done) ? text.slice(done.length) : text).trim()
          if (rest !== '') {
            speak(rest)
            spoken.current.set(node, text)
          }
          clearInterval(timer)
          timers.current.delete(node)
        }
      }, 150)
      timers.current.set(node, timer)
      lastChange.current.set(node, Date.now())
      const observer = new MutationObserver(() => lastChange.current.set(node, Date.now()))
      observer.observe(node, { childList: true, characterData: true, subtree: true })
    }
    document.querySelectorAll(RESPONSE).forEach((node) => seen.current.add(node))
    const root = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of Array.from(record.addedNodes)) {
          if (!(node instanceof Element)) continue
          if (node.matches(RESPONSE)) watch(node)
          node.querySelectorAll(RESPONSE).forEach(watch)
        }
      }
    })
    root.observe(document.body, { childList: true, subtree: true })
    return () => {
      root.disconnect()
      for (const timer of timers.current.values()) clearInterval(timer)
      timers.current.clear()
    }
  }, [on])

  const title = on ? (ru ? 'Не озвучивать ответы' : 'Stop reading answers aloud') : (ru ? 'Озвучивать ответы' : 'Read answers aloud')
  return h('button', {
    type: 'button',
    className: `nm-speak${on ? ' nm-speak-on' : ''}`,
    'aria-label': title,
    title,
    'aria-pressed': on,
    onMouseDown: (event: { preventDefault(): void }) => event.preventDefault(),
    onClick: () => {
      const next = !on
      patchSettings({ answers: next })
      if (!next) {
        queue.current.length = 0
        playing.current = false
      }
    },
  }, h('svg', { width: 17, height: 17, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.75, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
    h('path', { d: 'M4 9.5h3.5L12 5.5v13L7.5 14.5H4z' }),
    h('path', { d: on ? 'M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11' : 'M15.5 9a4 4 0 0 1 0 6' })))
}

export function apply(ctx: { slots: SlotsLike }): void {
  ensureStyle()
  ctx.slots.inject('conversation.input.right', () => {
    ctx.slots.register({ name: 'conversation.input.right', id: 'nanomuse-listen', order: 1 }, VoiceListen)
    ctx.slots.register({ name: 'conversation.input.right', id: 'nanomuse-speak', order: 2 }, VoiceAnswers)
  })
  // Beside the bundle's own Dictation section, never in its place: those settings
  // (the recognizer, the language, the model) stay where they were.
  ctx.slots.inject('settings.section', () =>
    ctx.slots.register({ name: 'settings.section', id: 'nanomuse-dictation', order: 24, label: () => 'Диктовка', locale: 'nanomuse' }, DictationSettings))
  ctx.slots.inject('settings.onboarding', () =>
    ctx.slots.register({ name: 'settings.onboarding', id: 'nanomuse-listen-bridge', order: -1 }, SettingsBridge))
  ctx.slots.inject('conversation.session.header.utilities', () =>
    ctx.slots.register({ name: 'conversation.session.header.utilities', id: 'nanomuse-chat-cloud', order: -30 }, ChatCloudButton))
}
