/**
 * Settings → From your phone: the one-time code that ties a messenger (Telegram, MAX) to this
 * computer's account, the two bots that take it, and the messengers already on the account.
 * The host half does the talking to the relay (`POST /link/code`, `GET /link/channels` in
 * `src/cloud.ts`); this only holds the code, the countdown and the list.
 */
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import { createElement as h, useCallback, useEffect, useState, type ReactNode } from 'react'
import { call, column, errorStyle, type LinkChannel, type LinkCode, type Translate, failureText, muted, row } from './api.ts'
import { openLink } from './bridge.ts'
import { useLive } from './live.ts'

/** The Telegram bot the code is sent to. */
const TELEGRAM_BOT = 'https://t.me/nanolika_bot'
/** The MAX bot. The address is a guess until the bot is published: fix this one line. */
const MAX_BOT = 'https://max.ru/id420517592685_bot'

/** `25.10`: the day and the month a messenger was linked, in the person's own numbers. */
function dayMonth(at: number): string {
  if (!at) return ''
  const date = new Date(at)
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}`
}

/** `12:34`: the clock time a code stops working at. */
function clockTime(at: number): string {
  const date = new Date(at)
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

/** `4:59`: what is left of a code, counting down. */
function leftOf(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

/** `Telegram`, `MAX`: the messenger's name as the relay sends it, or from the channel id. */
function nameOf(c: LinkChannel): string {
  if (c.label) return c.label
  if (c.channel === 'telegram') return 'Telegram'
  if (c.channel === 'max') return 'MAX'
  return c.channel
}

export function PhoneLink({ t }: { t: Translate }): ReactNode {
  const live = useLive()
  const signedIn = live.cloud.signedIn
  const [code, setCode] = useState<LinkCode | undefined>()
  const [channels, setChannels] = useState<LinkChannel[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | undefined>()
  const [copied, setCopied] = useState(false)
  // the countdown reads the clock twice a second, and stops when no code is shown
  const [now, setNow] = useState(() => Date.now())

  const readChannels = useCallback(() => {
    call<{ channels?: LinkChannel[] }>('link/channels')
      .then((body) => setChannels(Array.isArray(body.channels) ? body.channels : []))
      .catch(() => setChannels([]))
  }, [])

  const newCode = useCallback(() => {
    setBusy(true)
    setError(undefined)
    call<LinkCode>('link/code', {})
      .then((next) => { setCode(next); setNow(Date.now()) })
      .catch((err: unknown) => setError(failureText(t, err)))
      .finally(() => setBusy(false))
  }, [t])

  useEffect(() => {
    if (!signedIn) {
      setCode(undefined)
      setChannels([])
      return
    }
    newCode()
    readChannels()
  }, [signedIn, newCode, readChannels])

  useEffect(() => {
    if (!code?.expiresAt) return
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tick)
  }, [code])

  const copy = () => {
    if (!code?.code) return
    void navigator.clipboard?.writeText(code.code).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    }).catch(() => undefined)
  }

  if (!signedIn) {
    return h('div', { className: 'nm-section', 'data-testid': 'nm-phone-link' },
      h('p', null, t('plLead')),
      h('p', { className: 'nm-fine' }, t('plSignedOut')))
  }

  const left = code?.expiresAt ? code.expiresAt - now : 0
  const alive = Boolean(code?.code) && (!code?.expiresAt || left > 0)
  const timer = code?.expiresAt
    ? alive
      ? `${t('plUntil', { time: clockTime(code.expiresAt) })} · ${leftOf(left)}`
      : t('plExpired')
    : ''

  return h('div', { className: 'nm-section', 'data-testid': 'nm-phone-link' },
    h('p', null, t('plLead')),
    h('h2', null, t('plCodeTitle')),
    h('div', { className: 'nm-card' },
      h('div', { className: `nm-pl-code${alive ? '' : ' nm-pl-code-old'}` },
        h('span', null, code?.code || (busy ? '……' : '')),
        code?.code && alive
          ? h('button', { type: 'button', className: 'nm-pl-copy', onClick: copy }, copied ? t('plCopied') : t('plCopy'))
          : null),
      timer ? h('div', { style: muted }, timer) : null,
      h('p', { style: { margin: '10px 0 0' } }, t('plInstruction')),
      h('div', { style: { ...row, flexWrap: 'wrap', marginTop: 10 } },
        h(Button, { variant: 'primary', size: 'sm', disabled: busy, onClick: newCode }, busy ? t('plMaking') : t('plNewCode')),
        h(Button, { variant: 'outline', size: 'sm', onClick: () => openLink(TELEGRAM_BOT) }, t('plTelegram')),
        h(Button, { variant: 'outline', size: 'sm', onClick: () => openLink(MAX_BOT) }, t('plMax'))),
      error ? h('div', { style: errorStyle }, error) : null),
    h('h2', null, t('plChannelsTitle')),
    h('div', { className: 'nm-card' },
      channels.length === 0
        ? h('div', { className: 'nm-row' },
            h('div', { className: 'nm-row-main' },
              h('span', { className: 'nm-row-title' }, t('plNoChannels')),
              h('span', { className: 'nm-row-sub nm-wrap' }, t('plNoChannelsSub'))))
        : channels.map((c) => h('div', { key: `${c.channel}:${c.identifier}`, className: 'nm-row' },
            h('div', { className: 'nm-row-main' },
              h('span', { className: 'nm-row-title' }, `${nameOf(c)} · ${c.identifier}`),
              h('span', { className: 'nm-row-sub' }, c.linkedAt ? t('plLinked', { date: dayMonth(c.linkedAt) }) : t('plLinkedNoDate'))),
            h('span', { className: 'nm-state nm-state-on' }, t('plOn'))))),
    h('p', { className: 'nm-fine' }, t('plFine')))
}

/** The settings page around it (`src/client/index.ts` registers it as `nanomuse-phone`). */
export function makePhoneLinkSection(t: Translate) {
  return function PhoneLinkSection(): ReactNode {
    return h('div', { style: column }, h(PhoneLink, { t }))
  }
}
