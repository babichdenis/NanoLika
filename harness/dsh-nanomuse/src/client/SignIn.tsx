/**
 * The sign-in form in Settings → Account (phone number or e-mail → six-digit
 * code, or the account's password as the other way), the same steps the
 * first run walks through full-window. The host does the talking to the relay;
 * this only holds the form.
 */
import { Button, Input } from '@deepseek-ai/dsh-client-ui-primitives'
import { createElement as h, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useCloudConfig } from './AccountPage.tsx'
import { bridge } from './bridge.ts'
import { cash } from './money.ts'
import { call, column, errorStyle, muted, row, type CloudStatus, type Translate, failureText } from './api.ts'
import { useAccountOAuth } from './account-oauth.ts'
import qrcode from './vendor/qrcode.js'

/** A PKCE verifier and its S256 challenge, both base64url without padding. */
function pkce(): Promise<{ verifier: string; challenge: string }> {
  const bytes = crypto.getRandomValues(new Uint8Array(48))
  const verifier = btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)).then((digest) => {
    const challenge = btoa(String.fromCharCode(...new Uint8Array(digest))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
    return { verifier, challenge }
  })
}

/** The QR modules as one SVG path, or null when the link cannot be drawn. */
function qrOf(text: string): { path: string; size: number } | null {
  try {
    const qr = qrcode(0, 'M')
    qr.addData(text, 'Byte')
    qr.make()
    const modules = qr.getModuleCount()
    let path = ''
    for (let row = 0; row < modules; row++) for (let col = 0; col < modules; col++) if (qr.isDark(row, col)) path += `M${col + 4} ${row + 4}h1v1h-1z`
    return { path, size: modules + 8 }
  } catch {
    return null
  }
}

export interface SignInProps {
  t: Translate
  onSignedIn(status: CloudStatus): void
  /** Rendered under the form (the relay line, a "later" button…). */
  footer?: ReactNode
  /** The primary button takes the whole row (the welcome dialog). */
  wide?: boolean
}

export function SignIn({ t, onSignedIn, footer, wide = false }: SignInProps): ReactNode {
  const [step, setStep] = useState<'identifier' | 'code' | 'password' | 'telegram'>('identifier')
  // "Sign in with Telegram": the code on screen, the verifier kept here, the poll below.
  const [tg, setTg] = useState<{ code: string; url: string; verifier: string; expiresInS: number } | null>(null)
  const [identifier, setIdentifier] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  // A friend's invite code, as on the phone: optional, typed with the six-digit code; the relay
  // credits both when it is the first sign-in. Shown folded; a tap opens the field.
  const [invite, setInvite] = useState('')
  const [inviteOpen, setInviteOpen] = useState(false)
  const config = useCloudConfig()
  // A share link (nanolika://invite/CODE, from nanolika.ru's "Открыть в Лике"): the shell hands
  // the code over and the field opens with it filled, so nobody types eight characters by hand.
  useEffect(() => {
    const link = bridge()?.invite
    if (!link) return
    let alive = true
    const fill = (raw: string) => {
      const value = raw.replace(/[^0-9a-zA-Z-]/g, '').slice(0, 16).toUpperCase()
      if (!alive || !value) return
      setInvite(value)
      setInviteOpen(true)
    }
    link.take().then(fill).catch(() => undefined)
    const stop = link.subscribe(fill)
    return () => {
      alive = false
      stop()
    }
  }, [])
  const [busy, setBusy] = useState(false)
  const [resent, setResent] = useState(false)
  const [error, setError] = useState<string | undefined>()

  const oauth = useAccountOAuth(t, onSignedIn)

  const run = async (work: () => Promise<void>) => {
    oauth.cancel()
    setBusy(true)
    setError(undefined)
    try {
      await work()
    } catch (err: unknown) {
      setError(failureText(t, err))
    } finally {
      setBusy(false)
    }
  }

  const sendCode = (event?: FormEvent) => {
    event?.preventDefault()
    void run(async () => {
      await call('code', { identifier: identifier.trim() })
      setCode('')
      setStep('code')
    })
  }
  const resend = () => {
    void run(async () => {
      await call('code', { identifier: identifier.trim() })
      setResent(true)
      window.setTimeout(() => setResent(false), 4000)
    })
  }

  const openTelegram = () => {
    void run(async () => {
      const { verifier, challenge } = await pkce()
      const start = await call<{ code: string; url: string; expiresInS: number }>('telegram/start', { challenge })
      setTg({ ...start, verifier })
      setStep('telegram')
    })
  }

  // While the QR is on screen, ask the host whether the bot approved it; the host asks the relay.
  useEffect(() => {
    if (step !== 'telegram' || !tg) return
    let alive = true
    let timer = 0
    const deadline = Date.now() + Math.max(60, tg.expiresInS) * 1000
    const tick = async () => {
      if (!alive) return
      try {
        const status = await call<CloudStatus | null>('telegram/poll', { code: tg.code, verifier: tg.verifier })
        if (!alive) return
        if (status) {
          onSignedIn(status)
          return
        }
      } catch (err: unknown) {
        if (!alive) return
        if (Date.now() >= deadline) {
          setError(failureText(t, err))
          return
        }
      }
      if (Date.now() >= deadline) {
        setError(t('tgExpired'))
        return
      }
      timer = window.setTimeout(() => void tick(), 2000)
    }
    timer = window.setTimeout(() => void tick(), 1500)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [step, tg, onSignedIn, t])

  const signIn = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      try {
        const status = await call<CloudStatus>('verify', { identifier: identifier.trim(), code: code.trim(), invite: invite.trim().toUpperCase() })
        setCode('')
        onSignedIn(status)
      } catch (err: unknown) {
        setCode('')
        throw err
      }
    })
  }

  const login = (event: FormEvent) => {
    event.preventDefault()
    void run(async () => {
      const status = await call<CloudStatus>('login', { identifier: identifier.trim(), password })
      setPassword('')
      onSignedIn(status)
    })
  }

  const primary = { variant: 'primary' as const, size: 'md' as const, type: 'submit' as const, style: wide ? { width: '100%' } : undefined }
  const back = () => { setStep('identifier'); setError(undefined); setCode(''); setPassword('') }

  if (step === 'telegram') {
    const qr = tg ? qrOf(tg.url) : null
    return h('div', { style: column },
      h('div', { style: muted }, t('tgLead')),
      qr
        ? h('svg', { viewBox: `0 0 ${qr.size} ${qr.size}`, width: 232, height: 232, role: 'img', 'aria-label': t('tgSignIn'), style: { display: 'block', maxWidth: '100%', height: 'auto', background: '#fff', borderRadius: 12, margin: '16px auto' } }, h('rect', { width: '100%', height: '100%', fill: '#fff' }), h('path', { d: qr.path, fill: '#253020', shapeRendering: 'crispEdges' }))
        : null,
      tg ? h('div', null, h('a', { href: tg.url, target: '_blank', rel: 'noopener noreferrer', className: 'nm-ob-link nm-inline' }, t('tgOpen'))) : null,
      h('div', { style: muted }, t('tgWaiting')),
      error ? h('div', { style: errorStyle, role: 'alert' }, error) : null,
      h('div', { style: row },
        h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: back }, t('back'))))
  }

  if (step === 'code') {
    return h('form', { style: column, onSubmit: signIn },
      h('div', { style: muted },
        t('codeSentTo', { identifier: identifier.trim() }), ' ',
        h('button', { type: 'button', className: 'nm-ob-link nm-inline', disabled: busy, onClick: resend }, resent ? t('obResent') : t('obResend'))),
      h(Input, { value: code, onChange: (e: FormEvent<HTMLInputElement>) => setCode(e.currentTarget.value.replace(/\D/g, '').slice(0, 6)), placeholder: t('code'), inputMode: 'numeric', autoComplete: 'one-time-code', maxLength: 6, autoFocus: true, 'aria-label': t('code') }),
      inviteOpen
        ? h('div', { style: column },
            h(Input, { value: invite, onChange: (e: FormEvent<HTMLInputElement>) => setInvite(e.currentTarget.value.replace(/[^0-9a-zA-Z-]/g, '').slice(0, 16)), placeholder: t('obInviteCode'), autoComplete: 'off', autoCapitalize: 'characters', 'aria-label': t('obInviteCode') }),
            h('div', { style: muted }, t('obInviteHint', { bonus: cash(config.invitee_bonus_cny ?? 5, config) })))
        : h('div', null, h('button', { type: 'button', className: 'nm-ob-link nm-inline', disabled: busy, onClick: () => setInviteOpen(true) }, t('obHaveInvite'))),
      error ? h('div', { style: errorStyle, role: 'alert' }, error) : null,
      h('div', { style: row },
        h(Button, { ...primary, disabled: busy || code.trim().length !== 6 }, busy ? t('signingIn') : t('signIn')),
        h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: () => { setStep('password'); setError(undefined) } }, t('obOtherWay')),
        h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: back }, t('back'))),
      footer)
  }

  if (step === 'password') {
    return h('form', { style: column, onSubmit: login },
      h('div', { style: muted }, identifier.trim()),
      h(Input, { value: password, type: 'password', onChange: (e: FormEvent<HTMLInputElement>) => setPassword(e.currentTarget.value), placeholder: t('obPassword'), autoComplete: 'current-password', autoFocus: true, 'aria-label': t('obPassword') }),
      error ? h('div', { style: errorStyle, role: 'alert' }, error) : null,
      h('div', { style: row },
        h(Button, { ...primary, disabled: busy || password.length === 0 }, busy ? t('signingIn') : t('signIn')),
        h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: () => { setStep('code'); setError(undefined) } }, t('obUseCode')),
        h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: back }, t('back'))),
      footer)
  }

  return h('form', { style: column, onSubmit: sendCode },
    h('div', { style: row }, ...(['google', 'yandex'] as const).map((provider) => h(Button, {
      key: provider, type: 'button', variant: 'ghost', size: 'md', disabled: busy || oauth.busy,
      onClick: () => { void oauth.start(provider) },
    }, t(provider === 'google' ? 'frSignInGoogle' : 'frSignInYandex')))),
    oauth.error ? h('div', { style: errorStyle, role: 'alert' }, oauth.error) : null,
    oauth.busy ? h('div', { style: muted, role: 'status' }, t('oauthWaiting'), ' ', h(Button, { type: 'button', variant: 'ghost', size: 'md', onClick: oauth.cancel }, t('cancel'))) : null,
    h(Input, { value: identifier, onChange: (e: FormEvent<HTMLInputElement>) => setIdentifier(e.currentTarget.value), placeholder: t('identifier'), autoComplete: 'username', 'aria-label': t('identifier') }),
    h('div', { style: muted }, t('identifierHint')),
    error ? h('div', { style: errorStyle, role: 'alert' }, error) : null,
    h('div', { style: row },
      h(Button, { ...primary, disabled: busy || identifier.trim().length < 3 }, busy ? t('sending') : t('sendCode')),
      h(Button, { variant: 'ghost', size: 'md', type: 'button', disabled: busy, onClick: openTelegram }, t('tgSignIn'))),
    footer)
}
