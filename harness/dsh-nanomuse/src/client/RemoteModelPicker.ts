/**
 * The relay-hosted management sheet, opened from Settings → Models. It is the same
 * server page the chat pill uses: a full-window iframe over the app, with the vendor
 * list, filters and prices. The picker answers through `postMessage`; the caller gets
 * the model id and its kind and decides which slot to change.
 */
import { call } from './api.ts'
import { openLink } from './bridge.ts'

interface PickerSession {
  url: string
  key: string
}

export interface RemotePick {
  id: string
  kind: string
}

let activeLayer: HTMLElement | null = null
let opening = false

export async function openRemoteModelPicker(options: { kind?: string; selected?: string; title?: string } = {}): Promise<RemotePick | null> {
  if (activeLayer || opening) return null
  opening = true
  let session: PickerSession
  try {
    session = await call<PickerSession>('model-picker-session', {})
  } catch {
    opening = false
    return null
  }
  const kind = options.kind || 'chat'
  const title = options.title || 'Модели'
  const page = new URL('manage/', session.url)
  page.hash = new URLSearchParams({ key: session.key, sel: options.selected || '', kind, title }).toString()
  const origin = new URL(session.url).origin
  return new Promise((resolve) => {
    const layer = document.createElement('div')
    layer.className = 'nm-picker-layer'
    const frame = document.createElement('iframe')
    frame.className = 'nm-picker-frame'
    frame.src = page.href
    frame.title = title
    frame.setAttribute('allow', 'clipboard-write')
    frame.style.cssText = 'left:0;top:0;width:100vw;height:100vh'
    layer.append(frame)
    document.body.append(layer)
    activeLayer = layer
    opening = false
    let done = false
    const finish = (value: RemotePick | null): void => {
      if (done) return
      done = true
      window.removeEventListener('message', onMessage)
      window.removeEventListener('keydown', onKey)
      layer.remove()
      if (activeLayer === layer) activeLayer = null
      resolve(value)
    }
    // A way out even when the relay's page does not answer: the close sits on the app's own
    // layer, above the frame, and needs nothing from the server.
    const closer = document.createElement('button')
    closer.type = 'button'
    closer.className = 'nm-picker-close'
    closer.dataset.testid = 'nm-picker-close'
    closer.setAttribute('aria-label', 'Закрыть')
    closer.title = 'Закрыть'
    closer.textContent = '×'
    closer.style.cssText = `left:${Math.max(8, window.innerWidth - 48)}px;top:16px`
    closer.addEventListener('click', () => finish(null))
    const onKey = (event: KeyboardEvent): void => { if (event.key === 'Escape') finish(null) }
    window.addEventListener('keydown', onKey)
    layer.append(closer)
    const onMessage = (event: MessageEvent): void => {
      if (event.origin !== origin) return
      if (event.source !== frame.contentWindow) return
      const data = event.data as { type?: unknown; id?: unknown; kind?: unknown; url?: unknown } | null
      if (!data || typeof data !== 'object') return
      if (data.type === 'close') finish(null)
      else if (data.type === 'pick' && typeof data.id === 'string' && data.id) {
        finish({ id: data.id, kind: typeof data.kind === 'string' ? data.kind : '' })
      } else if (data.type === 'plan') {
        const url = typeof data.url === 'string' && data.url ? new URL(data.url, session.url).href : 'https://cloud.nanolika.ru/web/account/?pay=1&plan=lika-plus&source=settings'
        openLink(url)
      }
    }
    window.addEventListener('message', onMessage)
    frame.addEventListener('load', () => frame.contentWindow?.focus())
  })
}
