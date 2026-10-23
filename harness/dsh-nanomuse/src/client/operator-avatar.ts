import { useEffect, useState } from 'react'
import { call, type CloudStatus } from './api.ts'
async function loadBase(): Promise<string> { return (await call<CloudStatus>('status')).baseURL }

interface Look { revision: number; active: string | null; stills: Record<string, string>; clips: Record<string, string> }
let current: Look | null = null
const listeners = new Set<(look: Look | null) => void>()
let timer: ReturnType<typeof setInterval> | undefined
let checking = false
async function check(): Promise<void> {
  if (checking) return
  checking = true
  try {
    const base = await loadBase()
    const origin = new URL(base.replace(/\/v1\/?$/, '') + '/')
    if (origin.protocol !== 'https:' || origin.username || origin.password) throw new Error('avatar_origin')
    const response = await fetch(new URL('v1/brand/avatar', origin), { cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(6000) })
    if (!response.ok) throw new Error('avatar_manifest')
    const raw = await response.json() as Look
    if (raw.active === null) current = null
    else {
      if (!/^[a-f0-9]{24}$/.test(raw.active)) throw new Error('avatar_id')
      const urls = (entries: Record<string, string>, extension: string): Record<string, string> => Object.fromEntries(Object.entries(entries).map(([mood, value]) => {
        if (!['idle', 'working', 'waiting', 'happy', 'error'].includes(mood) || value !== `/v1/brand/avatar/assets/${raw.active}/${mood}.${extension}`) throw new Error('avatar_path')
        return [mood, new URL(value, origin).href]
      }))
      if (!raw.stills?.idle) throw new Error('avatar_missing')
      current = { revision: raw.revision, active: raw.active, stills: urls(raw.stills, 'webp'), clips: urls(raw.clips || {}, 'mp4') }
    }
  } catch { current = null }
  finally { checking = false; for (const listener of listeners) listener(current) }
}
export function useOperatorAvatar(enabled: boolean): Look | null {
  const [look, setLook] = useState<Look | null>(current)
  useEffect(() => {
    if (!enabled) return
    listeners.add(setLook)
    setLook(current)
    if (!timer) { void check(); timer = setInterval(() => { if (document.visibilityState !== 'hidden') void check() }, 60000) }
    return () => { listeners.delete(setLook); if (!listeners.size && timer) { clearInterval(timer); timer = undefined } }
  }, [enabled])
  return enabled ? look : null
}
