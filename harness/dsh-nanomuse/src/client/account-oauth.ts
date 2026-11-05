/** Shared browser sign-in for onboarding and Settings. The host keeps all credentials. */
import { useEffect, useRef, useState } from 'react'
import { call, failureText, type CloudStatus, type Translate } from './api.ts'
import { openLink } from './bridge.ts'

export function useAccountOAuth(t: Translate, signedIn: (status: CloudStatus) => Promise<void> | void) {
  const active = useRef<string | undefined>(undefined)
  const mounted = useRef(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | undefined>(undefined)
  const cancel = () => {
    const id = active.current
    active.current = undefined
    if (id) void call('oauth/cancel', { id }).catch(() => undefined)
    if (mounted.current) setBusy(false)
  }
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; cancel() }
  }, [])
  const start = async (provider: 'google' | 'yandex') => {
    cancel()
    const id = crypto.randomUUID()
    active.current = id
    setBusy(true)
    setError(undefined)
    try {
      const { url } = await call<{ url: string }>('oauth/start', { provider, id })
      if (active.current !== id) { await call('oauth/cancel', { id }); return }
      openLink(url)
      while (active.current === id) {
        const result = await call<{ phase: string; error?: string; status?: CloudStatus }>('oauth/status', { id })
        if (active.current !== id) return
        if (result.phase === 'complete' && result.status) {
          active.current = undefined
          await signedIn(result.status)
          return
        }
        if (result.phase === 'failed') throw { code: result.error ?? 'oauth_failed' }
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
      }
    } catch (err) {
      if (active.current === id && mounted.current) setError(failureText(t, err))
    } finally {
      if (active.current === id) cancel()
      if (mounted.current && !active.current) setBusy(false)
    }
  }
  return { busy, error, start, cancel }
}
