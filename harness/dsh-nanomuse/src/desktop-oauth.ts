/** Browser sign-in with an ephemeral IPv4 loopback listener and PKCE. */
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto'
import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'

export interface DesktopOAuth {
  url: string
  cancel(): void
}

export async function desktopOAuth(options: {
  origin: string
  provider: 'google' | 'yandex'
  exchange(code: string, verifier: string, signal: AbortSignal): Promise<void>
  failed(reason: 'cancelled' | 'timeout' | 'denied' | 'failed'): void
  timeoutMs?: number
}): Promise<DesktopOAuth> {
  const origin = new URL(options.origin)
  if (origin.protocol !== 'https:' && !(origin.protocol === 'http:' && origin.hostname === '127.0.0.1')) throw new Error('Sign-in requires an HTTPS relay')
  const state = randomBytes(32).toString('base64url')
  const verifier = randomBytes(48).toString('base64url')
  const challenge = createHash('sha256').update(verifier).digest('base64url')
  const abort = new AbortController()
  let used = false
  let finished = false
  let expectedHost = ''
  let timer: ReturnType<typeof setTimeout> | undefined
  const close = () => { if (timer) clearTimeout(timer); server.close(); server.closeIdleConnections() }
  const fail = (reason: Parameters<typeof options.failed>[0]) => {
    if (finished) return
    finished = true
    used = true
    abort.abort()
    close()
    options.failed(reason)
  }
  const server = createServer((req, res) => {
    res.setHeader('Cache-Control', 'no-store')
    res.setHeader('Referrer-Policy', 'no-referrer')
    res.setHeader('Content-Type', 'text/plain; charset=utf-8')
    res.setHeader('X-Content-Type-Options', 'nosniff')
    let url: URL
    try { url = new URL(req.url ?? '/', 'http://127.0.0.1') } catch { res.writeHead(400).end('Invalid sign-in response'); return }
    const received = url.searchParams.get('state') ?? ''
    if (req.method !== 'GET' || req.headers.host !== expectedHost || url.pathname !== '/nanolika-signin' || used || !/^[A-Za-z0-9_-]{43}$/.test(received) || !timingSafeEqual(Buffer.from(received), Buffer.from(state))) {
      res.writeHead(400).end('Invalid sign-in response')
      return
    }
    used = true
    if (url.searchParams.has('error')) { res.writeHead(400).end('Sign-in cancelled. Вход отменён. 登录已取消。'); fail('denied'); return }
    const code = url.searchParams.get('code') ?? ''
    if (!/^[A-Za-z0-9_-]{43,128}$/.test(code)) { res.writeHead(400).end('Invalid sign-in response'); fail('failed'); return }
    void options.exchange(code, verifier, abort.signal).then(() => {
      if (abort.signal.aborted) return
      finished = true
      res.end('Signed in. Return to NanoLika. Вход выполнен. Вернитесь в Лику. 登录成功，请返回 NanoLika。')
      close()
    }).catch(() => {
      res.writeHead(400).end('Sign-in failed. Return to NanoLika. Не удалось войти. Вернитесь в Лику. 登录失败，请返回 NanoLika。')
      fail('failed')
    })
  })
  server.headersTimeout = 10_000
  server.requestTimeout = 30_000
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const port = (server.address() as AddressInfo).port
  expectedHost = `127.0.0.1:${port}`
  const url = new URL(`/v1/auth/oauth/${options.provider}/start`, origin)
  url.searchParams.set('desktop_callback', `http://${expectedHost}/nanolika-signin`)
  url.searchParams.set('desktop_challenge', challenge)
  url.searchParams.set('desktop_state', state)
  timer = setTimeout(() => fail('timeout'), options.timeoutMs ?? 300_000)
  timer.unref()
  return { url: url.href, cancel: () => fail('cancelled') }
}
