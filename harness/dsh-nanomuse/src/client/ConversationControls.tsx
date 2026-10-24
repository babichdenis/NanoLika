/** Account chat navigation. These buttons never send a prompt to the model. */
import { createElement as h, useEffect, useRef, useState, type ReactNode } from 'react'
import { call, type Translate } from './api.ts'

export function ConversationControls({ t, sessionId }: { t: Translate; sessionId: string | null }): ReactNode {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const selected = useRef(sessionId)
  selected.current = sessionId
  useEffect(() => { setNote('') }, [sessionId])
  const open = async (channel: 'telegram' | 'max'): Promise<void> => {
    if (!sessionId || busy) return
    const target = sessionId
    setBusy(true)
    setNote('')
    try {
      const result = await call<{ opened: boolean; channel: string }>('conversation/open', { sessionId, channel })
      if (result.opened !== true || result.channel !== channel) throw new Error(t('chOpenFailed'))
      if (selected.current === target) setNote(t(channel === 'telegram' ? 'chTelegramReady' : 'chMaxReady'))
    } catch (error: unknown) {
      if (selected.current === target) setNote(error instanceof Error ? error.message : t('chOpenFailed'))
    } finally { setBusy(false) }
  }
  return h('div', { className: 'nm-conversation-controls' },
    h('div', { className: 'nm-conversation-chips', role: 'group', 'aria-label': t('railChats') },
      h('button', { type: 'button', onClick: () => { void open('telegram') }, disabled: busy || !sessionId }, t('chOpenTelegram')),
      h('button', { type: 'button', onClick: () => { void open('max') }, disabled: busy || !sessionId }, t('chContinueMax'))),
    busy || note ? h('div', { className: 'nm-conversation-note', role: 'status' }, busy ? t('chOpening') : note) : null)
}
