/**
 * Наш микрофон: запись без браузерного декодирования.
 *
 * Почему не встроенный голосовой ввод. Он пишет через `MediaRecorder`, складывает куски в
 * `Blob` и декодирует их `decodeAudioData`. В Electron этот вызов отвечает «External buffers
 * are not allowed» на буфер из Blob, и диктовка не работает совсем — ни разу, при любой
 * записи. Здесь звук берётся как есть: `AudioContext` отдаёт Float32, мы сами приводим его к
 * 16 кГц и собираем WAV. Никакого декодирования в браузере нет, поэтому и отказывать нечему.
 *
 * Распознаёт по-прежнему локальный Whisper: WAV уходит на хост в наш же маршрут `/voice`.
 */
import { createElement as h, useRef, useState, type ReactNode } from 'react'
import type { Translate } from './api.ts'
import { call } from './api.ts'
import { prefillComposer } from './composer.ts'
import { IconMic, IconWave } from './icons.tsx'

/** Частота, которую ждёт распознаватель: 16 кГц, один канал. */
const RATE = 16000

/** Сколько секунд пишем максимум: дольше — уже не реплика, а совещание. */
const MAX_SECONDS = 120

/** Собрать WAV (PCM16, моно) из сэмплов. */
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

/** Привести запись к 16 кГц: линейная интерполяция, без сторонних буферов. */
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

/** В base64: маршрут принимает WAV строкой, чтобы не возиться с бинарным телом. */
function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]!)
  return btoa(binary)
}

interface Run {
  stop(): void
}

/**
 * Детектор реплики по громкости: пока слышно речь — копим, после паузы — отдаём на распознавание.
 * `silenceMs` — сколько тишины считать концом фразы, `minMs` — короче этого не отправляем.
 */
function makeSpeechSplitter(onUtterance: (samples: Float32Array) => void, settings: { silenceMs: number; minMs: number; maxMs?: number }) {
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
    /** Deaf for a while: the answer being spoken, or a turn already running. */
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
      // The bar follows the room: a quiet one needs almost nothing, and a television that
      // is always on lifts the floor instead of being taken for speech every phrase.
      if (!voicing) floor = floor * 0.97 + rms * 0.03
      const voice = rms > Math.max(0.012, floor * 3)
      if (voice) {
        voicing = true
        silence = 0
        parts.push(Float32Array.from(samples))
        heard += samples.length
        // a monologue still goes out in one piece instead of growing without a bound
        if ((heard / sampleRate) * 1000 >= (settings.maxMs ?? 30_000)) {
          onUtterance(collect())
          reset()
        }
        return
      }
      if (!voicing) return
      // тишина после речи: копим её коротко, чтобы не обрезать окончание слова
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

/**
 * Кнопка постоянного слушания. Раз включили — микрофон не закрывается: каждая реплика,
 * отделённая паузой, уходит на распознавание и появляется в строке ввода. Выключили —
 * остаток сказанного всё равно распознаётся, а микрофон отпускается.
 */
export function makeVoiceListen(t: Translate, hooks: { onError?: (message: string) => void; onState?: (state: 'idle' | 'listening' | 'working') => void } = {}) {
  return function VoiceListen(props: { inputActions?: { captureInsertion(): unknown; insertText(text: string, span: unknown): boolean; setDraft(text: string): void; submit(): void } } = {}): ReactNode {
    const [state, setState] = useState<'idle' | 'listening' | 'working'>('idle')
    const [failure, setFailure] = useState('')
    const run = useRef<{ stop(): void } | null>(null)
    // the audio callback outlives this render, so what the microphone must ignore lives in a ref
    const busy = useRef(false)
    const change = (next: 'idle' | 'listening' | 'working') => {
      busy.current = next === 'working'
      setState(next)
      hooks.onState?.(next)
    }
    const fail = (message: string) => {
      run.current = null
      change('idle')
      setFailure(message)
      hooks.onError?.(message)
    }

    /** Put the phrase where the person would have typed it and send it, as a live conversation does. */
    const deliver = (text: string) => {
      const actions = props.inputActions
      if (actions === undefined) {
        prefillComposer(text)
        return
      }
      try {
        const span = actions.captureInsertion()
        if (!actions.insertText(text, span)) actions.setDraft(text)
        actions.submit()
      } catch {
        prefillComposer(text)
      }
    }

    const send = (samples: Float32Array, rate: number) => {
      const wave = encodeWave(resample(samples, rate))
      change('working')
      void call<{ text?: string }>('voice', { audio: toBase64(wave), language: 'ru' })
        .then((answer) => {
          const text = (answer.text ?? '').trim()
          if (text !== '') deliver(text)
          setFailure('')
          if (run.current !== null) change('listening')
        })
        .catch((error: unknown) => {
          const message = error instanceof Error ? error.message : 'Распознавание не отвечает'
          setFailure(message)
          hooks.onError?.(message)
          if (run.current !== null) change('listening')
        })
    }

    const start = async () => {
      try {
        // Echo cancellation and noise suppression are the browser's own: without them a
        // laptop's speakers feed the answer straight back into the microphone.
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
        const context = new AudioContext()
        if (context.state !== 'running') await context.resume().catch(() => undefined)
        const source = context.createMediaStreamSource(stream)
        const processor = context.createScriptProcessor(4096, 1, 1)
        const splitter = makeSpeechSplitter((samples) => send(samples, context.sampleRate), { silenceMs: 900, minMs: 350 })
        // the host knows when the platform's voice is talking; a listener that cannot tell
        // its own answer from a question answers itself until the conversation is a mess
        const talking = { active: false }
        const askTalking = setInterval(() => {
          void call<{ speaking?: boolean }>('speaking', {})
            .then((answer) => {
              talking.active = answer.speaking === true
            })
            .catch(() => undefined)
        }, 700)
        processor.onaudioprocess = (event) => {
          splitter.mute(busy.current || talking.active)
          splitter.push(event.inputBuffer.getChannelData(0), context.sampleRate)
        }
        source.connect(processor)
        processor.connect(context.destination)

        let watchdog: ReturnType<typeof setInterval> | undefined
        const finish = () => {
          if (watchdog !== undefined) clearInterval(watchdog)
          clearInterval(askTalking)
          processor.disconnect()
          source.disconnect()
          splitter.flush()
          stream.getTracks().forEach((item) => item.stop())
          void context.close().catch(() => undefined)
          run.current = null
          change('idle')
          setFailure('')
        }

        // Electron pauses audio in a hidden window; a sleeping context looks exactly like
        // "the button is on and nothing happens". Wake it, and stop honestly if the device went.
        const track = stream.getAudioTracks()[0]
        watchdog = setInterval(() => {
          if (context.state !== 'running') void context.resume().catch(() => undefined)
          if (track !== undefined && track.readyState === 'ended') finish()
        }, 1500)

        run.current = { stop: finish }
        change('listening')
      } catch (error) {
        fail(error instanceof Error ? error.message : 'Микрофон недоступен')
      }
    }

    const active = state !== 'idle'
    const title = failure !== '' ? failure : active ? t('continuousStop') : t('continuousStart')
    return h('button', {
      type: 'button',
      className: `nm-mic nm-listen${active ? ' nm-live' : ''}${failure !== '' ? ' nm-failed' : ''}`,
      'aria-label': active ? t('continuousStop') : t('continuousStart'),
      title,
      'aria-pressed': active,
      onMouseDown: (event: { preventDefault(): void }) => event.preventDefault(),
      onClick: () => {
        if (state !== 'idle') run.current?.stop()
        else void start()
      },
    }, h(IconWave, { size: 17 }))
  }
}
