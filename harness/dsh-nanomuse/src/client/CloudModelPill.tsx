/**
 * The model pill of the composer: what answers now, and the relay-hosted catalogue
 * behind it. The catalogue itself is the relay's page, embedded in an iframe over the
 * app — the shell only positions the frame and takes the answer, so a change on the
 * server reaches every installed app without a new bundle.
 */
import { createElement as h, Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { call, type Translate } from './api.ts'
import { openLink } from './bridge.ts'
import { useLive } from './live.ts'
import { useModels } from './ModelsSection.tsx'
import { peekWin, useWin } from './win.ts'

interface PickerSession {
  url: string
  key: string
  expiresAt?: number
}

interface Box {
  left: number
  top: number
  width: number
  height: number
}

export function makeCloudModelPill(t: Translate) {
  return function CloudModelPill(): ReactNode {
    const { view, reload } = useModels(t)
    const live = useLive()
    // The chat on screen has its own model (C7): a picture model picked in one chat stays
    // there. A chat that never chose follows the default, as it always did.
    const session = peekWin().current ?? ''
    useWin()
    const chatModel = live.sync?.models?.[session] ?? ''
    const shown = chatModel || (view?.slots.chat?.provider === 'nanomuse' ? view.slots.chat.model : '')
    const [busy, setBusy] = useState(false)
    const [sheet, setSheet] = useState<PickerSession | null>(null)
    const [box, setBox] = useState<Box | null>(null)
    const [manage, setManage] = useState(false)
    const button = useRef<HTMLButtonElement>(null)
    const frame = useRef<HTMLIFrameElement>(null)
    const slot = view?.slots.chat

    // The frame is a true in-app popover: sized like the app window (a little shorter),
    // anchored to the pill, and transparent around the relay's card.
    useLayoutEffect(() => {
      if (!sheet) {
        setBox(null)
        return
      }
      if (manage) {
        const full = (): void => setBox({ left: 0, top: 0, width: window.innerWidth, height: window.innerHeight })
        full()
        window.addEventListener('resize', full)
        return () => window.removeEventListener('resize', full)
      }
      const place = (): void => {
        const pill = button.current?.getBoundingClientRect()
        if (!pill) return
        // Wide enough for the card and the model's tip beside it: at 860 the tip had nowhere
        // to go but over the list, which is what made it look like it hid the choice.
        const width = Math.min(940, window.innerWidth - 24)
        const height = Math.min(780, window.innerHeight - 24)
        let left = Math.round(pill.left - 16)
        left = Math.min(left, window.innerWidth - width - 12)
        left = Math.max(12, left)
        let top = Math.round(pill.top - height - 8)
        if (top < 12) top = Math.max(12, Math.round((window.innerHeight - height) / 2))
        setBox({ left, top, width, height })
      }
      place()
      window.addEventListener('resize', place)
      return () => window.removeEventListener('resize', place)
    }, [sheet, manage])

    useEffect(() => {
      if (!sheet) return
      const origin = new URL(sheet.url).origin
      const onMessage = (event: MessageEvent): void => {
        if (event.origin !== origin) return
        if (frame.current && event.source !== frame.current.contentWindow) return
        const data = event.data as { type?: unknown; id?: unknown; url?: unknown } | null
        if (!data || typeof data !== 'object') return
        if (data.type === 'close') {
          setSheet(null)
          setManage(false)
        } else if (data.type === 'pick' && typeof data.id === 'string' && data.id) {
          const id = data.id
          setSheet(null)
          setManage(false)
          // this chat, not the default for new chats: the pick goes with the conversation
          // and reaches the browser, Telegram and MAX through the account
          if (id !== shown) void call('chat-model', { provider: 'nanomuse', model: id, sessionId: session }).then(() => reload()).catch(() => undefined)
        } else if (data.type === 'pick-other') {
          // A picture or a clip went to its own slot; the chat model is untouched, and the
          // Settings → Models page should show the new choice without a reload.
          void reload()
        } else if (data.type === 'manage') {
          // The same web panel as Settings in the web client, inside this iframe.
          setManage(true)
          setSheet((prev) => prev ? { ...prev, url: new URL('manage/', prev.url).href } : prev)
        } else if (data.type === 'plan') {
          const url = typeof data.url === 'string' && data.url ? new URL(data.url, sheet.url).href : 'https://cloud.nanolika.ru/web/account/?pay=1&plan=lika-plus&source=picker'
          openLink(url)
        }
      }
      window.addEventListener('message', onMessage)
      return () => window.removeEventListener('message', onMessage)
    }, [sheet, shown, session, reload])

    if (!view?.signedIn || !view.cloudModels || !shown) return null
    const name = slot?.options.find((option) => option.id === shown)?.name ?? shown
    const open = async (): Promise<void> => {
      setBusy(true)
      setManage(false)
      try {
        setSheet(await call<PickerSession>('model-picker-session', {}))
      } catch {
        // the pill stays as it was; the next click asks the host again
      } finally {
        setBusy(false)
      }
    }
    // No `kind` here: the picker lists what the person keeps in favourites, and a picture or
    // a clip among them is assigned to its own slot — hiding those kinds made the stars for
    // pictures and video pointless in this window.
    const src = sheet
      ? `${sheet.url}#${new URLSearchParams({ key: sheet.key, sel: shown, title: t('mlTitle') }).toString()}`
      : ''
    return h(
      Fragment,
      null,
      h(
        'button',
        {
          ref: button,
          type: 'button',
          className: 'nm-pill nm-pill-ghost nm-pill-sm nm-model-pill',
          'data-testid': 'nm-model-pill',
          disabled: busy,
          title: `${t('mlCloudModels')}: ${name}`,
          'aria-label': `${t('mlCloudModels')}: ${name}`,
          onClick: () => void open(),
        },
        name,
      ),
      sheet && box
        ? createPortal(
            h(
              'div',
              {
                className: 'nm-picker-layer',
                role: 'dialog',
                'aria-modal': 'true',
                'aria-label': t('mlTitle'),
                onMouseDown: (event: { target: EventTarget | null; currentTarget: EventTarget | null }) => { if (event.target === event.currentTarget) setSheet(null) },
              },
              h('iframe', {
                ref: frame,
                className: 'nm-picker-frame',
                src,
                title: t('mlTitle'),
                style: { left: box.left, top: box.top, width: box.width, height: box.height },
                allow: 'clipboard-write',
              }),
              // A way out even when the relay's page does not answer: the close sits on the
              // app's own layer, above the frame, and needs nothing from the server.
              h('button', {
                type: 'button',
                className: 'nm-picker-close',
                'data-testid': 'nm-picker-close',
                'aria-label': t('close'),
                title: t('close'),
                style: { left: box.left + box.width - 44, top: box.top + 8 },
                onClick: () => { setSheet(null); setManage(false) },
              }, '×'),
            ),
            document.body,
          )
        : null,
    )
  }
}
