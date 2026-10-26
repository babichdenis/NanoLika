/** Optional account-bound QR links for Telegram and MAX. */
import { Button } from '@deepseek-ai/dsh-client-ui-primitives'
import { createElement as h, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { call, column, errorStyle, type LinkChannel, type ChannelStart, type Translate, failureText, muted, row } from './api.ts'
import { openLink } from './bridge.ts'
import { BOT_LINKS, connectionQR } from './channel-qr.ts'
import { useLive } from './live.ts'

export function PhoneLink({ t }: { t: Translate }): ReactNode {
  const live = useLive()
  const identity = live.cloud.signedIn ? live.cloud.hint : undefined
  const [start, setStart] = useState<ChannelStart>()
  const [channels, setChannels] = useState<LinkChannel[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [now, setNow] = useState(Date.now())
  const revision = useRef(0)
  const qr = useMemo(() => start ? connectionQR(start) : undefined, [start])

  useEffect(() => {
    let active = true
    revision.current++
    setStart(undefined)
    setChannels([])
    setBusy(false)
    setError(undefined)
    if (!live.cloud.signedIn) return
    const refresh = async () => {
      if (document.visibilityState === 'hidden') return
      try {
        const body = await call<{ channels?: LinkChannel[] }>('link/channels')
        if (active) setChannels(Array.isArray(body.channels) ? body.channels : [])
      } catch { /* Keep the last confirmed list on a temporary network failure. */ }
    }
    void refresh()
    const poll = window.setInterval(() => { setNow(Date.now()); void refresh() }, 3000)
    return () => { active = false; revision.current++; window.clearInterval(poll) }
  }, [identity, live.cloud.signedIn])

  const connect = async (channel: 'telegram' | 'max') => {
    const request = ++revision.current
    setStart(undefined)
    setBusy(true)
    setError(undefined)
    try {
      const next = await call<ChannelStart>('link/start', { channel })
      connectionQR(next) // Validate the bot destination before displaying a clickable link.
      if (request === revision.current) { setStart(next); setNow(Date.now()) }
    } catch (err) { if (request === revision.current) setError(failureText(t, err)) }
    finally { if (request === revision.current) setBusy(false) }
  }

  /* Letting a chat go: the button asks first, because the chat stops reaching this account and
   * going back needs a fresh code. A chat that *is* the account is refused by the relay
   * (`channel_is_the_account`), and its own sentence says so instead of an error. */
  const unlink = async (channel: string) => {
    if (!window.confirm(t('plUnlinkAsk'))) return
    const request = ++revision.current
    setBusy(true)
    setError(undefined)
    try {
      await call('link/unlink', { channel })
      const body = await call<{ channels?: LinkChannel[] }>('link/channels')
      if (request !== revision.current) return
      setChannels(Array.isArray(body.channels) ? body.channels : [])
      setStart(undefined)
    } catch (err) { if (request === revision.current) setError(failureText(t, err)) }
    finally { if (request === revision.current) setBusy(false) }
  }

  if (!live.cloud.signedIn) return h('div', { className: 'nm-section', 'data-testid': 'nm-phone-link' }, h('p', null, t('plLead')), h('p', { className: 'nm-fine' }, t('plSignedOut')))
  const connected = !!start && channels.some(c => c.channel === start.channel)
  const left = Math.max(0, Math.ceil(((start?.expiresAt ?? 0) - now) / 1000))
  const alive = !!start && left > 0 && !connected
  const name = start?.channel === 'telegram' ? 'Telegram' : 'MAX'
  return h('div', { className: 'nm-section', 'data-testid': 'nm-phone-link' },
    h('p', null, t('plLead')),
    h('div', { style: { ...row, flexWrap: 'wrap' } },
      ...(['telegram', 'max'] as const).map(channel => h(Button, { key: channel, variant: start?.channel === channel ? 'primary' : 'outline', size: 'sm', disabled: busy, onClick: () => { void connect(channel) } }, channel === 'telegram' ? 'Telegram' : 'MAX'))),
    busy ? h('p', { role: 'status' }, t('plMaking')) : null,
    start ? h('div', { className: 'nm-card', style: { marginTop: 14 } },
      h('h2', null, connected ? t('plConnected', { name }) : t('plCodeTitle', { name })),
      alive && qr ? h('svg', { viewBox: `0 0 ${qr.size} ${qr.size}`, width: 232, height: 232, role: 'img', 'aria-label': `QR: ${name}`, style: { display: 'block', maxWidth: '100%', height: 'auto', background: '#fff', borderRadius: 12, margin: '16px auto' } }, h('rect', { width: '100%', height: '100%', fill: '#fff' }), h('path', { d: qr.path, fill: '#253020', shapeRendering: 'crispEdges' })) : null,
      alive ? h('p', null, t('plInstruction')) : null,
      alive ? h('p', { style: muted, role: 'status' }, t('plRemaining', { time: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` })) : null,
      !alive && !connected ? h('p', { role: 'status' }, t('plExpired')) : null,
      alive || connected ? h(Button, { variant: 'primary', size: 'sm', onClick: () => openLink(connected ? BOT_LINKS[start.channel] : start.url) }, start.channel === 'telegram' ? t('plTelegram') : t('plMax')) : h(Button, { variant: 'primary', size: 'sm', onClick: () => { void connect(start.channel) } }, t('plNewCode')),
      alive ? h('p', { className: 'nm-fine' }, t('plFine')) : null) : null,
    error ? h('p', { style: errorStyle, role: 'alert' }, error) : null,
    h('h2', null, t('plChannelsTitle')),
    h('div', { className: 'nm-card' }, channels.length ? channels.map(c => h('div', { key: c.channel, className: 'nm-row' }, h('span', { className: 'nm-row-title' }, c.label || (c.channel === 'telegram' ? 'Telegram' : 'MAX')), h('span', { className: 'nm-state nm-state-on' }, t('plOn')), h('span', { style: { marginLeft: 'auto' } }, h(Button, { variant: 'outline', size: 'sm', disabled: busy, onClick: () => { void unlink(c.channel) } }, t('plUnlink')))) ) : h('p', null, t('plNoChannelsSub'))))
}

export function makePhoneLinkSection(t: Translate) {
  return function PhoneLinkSection(): ReactNode {
    return h('div', { style: column }, h(PhoneLink, { t }))
  }
}
