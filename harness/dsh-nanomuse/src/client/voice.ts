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
import { IconMic } from './icons.tsx'

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
 * Кнопка микрофона. `onError` получает короткое объяснение, `onBusy` — состояние для UI.
 */
export function makeVoiceMic(t: Translate, hooks: { onError?: (message: string) => void; onState?: (state: 'idle' | 'recording' | 'working') => void } = {}) {
  return function VoiceMic(): ReactNode {
    const [state, setState] = useState<'idle' | 'recording' | 'working'>('idle')
    const run = useRef<Run | null>(null)
    const change = (next: 'idle' | 'recording' | 'working') => {
      setState(next)
      hooks.onState?.(next)
    }
    const fail = (message: string) => {
      change('idle')
      hooks.onError?.(message)
    }

    const start = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        const context = new AudioContext()
        const source = context.createMediaStreamSource(stream)
        const processor = context.createScriptProcessor(4096, 1, 1)
        const parts: Float32Array[] = []
        let held = 0
        processor.onaudioprocess = (event) => {
          const input = event.inputBuffer.getChannelData(0)
          if (held >= context.sampleRate * MAX_SECONDS) return
          parts.push(Float32Array.from(input))
          held += input.length
        }
        source.connect(processor)
        processor.connect(context.destination)

        const finish = (send: boolean) => {
          processor.disconnect()
          source.disconnect()
          stream.getTracks().forEach((track) => track.stop())
          const rate = context.sampleRate
          void context.close().catch(() => undefined)
          run.current = null
          if (!send) return change('idle')
          const total = parts.reduce((sum, part) => sum + part.length, 0)
          if (total === 0) return fail('Ничего не записалось')
          const all = new Float32Array(total)
          let at = 0
          for (const part of parts) {
            all.set(part, at)
            at += part.length
          }
          change('working')
          const wave = encodeWave(resample(all, rate))
          void call<{ text?: string }>('voice', { audio: toBase64(wave), language: 'ru' })
            .then((answer) => {
              const text = (answer.text ?? '').trim()
              if (text === '') {
                change('idle')
                hooks.onError?.('Речь не разобрана: попробуйте ближе к микрофону')
                return
              }
              prefillComposer(text)
              change('idle')
            })
            .catch((error: unknown) => {
              fail(error instanceof Error ? error.message : 'Распознавание не отвечает')
            })
        }

        run.current = { stop: () => finish(true) }
        change('recording')
      } catch (error) {
        fail(error instanceof Error ? error.message : 'Микрофон недоступен')
      }
    }

    return h('button', {
      type: 'button',
      className: `nm-mic${state === 'recording' ? ' nm-live' : ''}`,
      'aria-label': state === 'recording' ? t('micStop') : t('micStart'),
      title: state === 'recording' ? t('micStop') : state === 'working' ? t('working') : t('micStart'),
      'aria-pressed': state === 'recording',
      disabled: state === 'working',
      onMouseDown: (event: { preventDefault(): void }) => event.preventDefault(),
      onClick: () => {
        if (state === 'recording') run.current?.stop()
        else if (state === 'idle') void start()
      },
    }, h(IconMic, { size: 17 }))
  }
}
