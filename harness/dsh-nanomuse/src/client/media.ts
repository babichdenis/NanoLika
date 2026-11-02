/**
 * What a link or an attachment is, for the side pane and the picture viewer: the kind
 * by mime when the relay told us, by the file's tail otherwise. One answer for the
 * click on a link in the chat, a chip under a messenger's message and the pane's own
 * download button, so all three agree on what they are looking at.
 */
import type { PreviewKind } from './preview.ts'

const IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'bmp', 'heic', 'heif'])
const VIDEO = new Set(['mp4', 'm4v', 'webm', 'mov', 'mkv', 'avi'])
const AUDIO = new Set(['mp3', 'm4a', 'wav', 'ogg', 'oga', 'opus', 'flac', 'aac'])

/** The pane's kind for a URL and, when known, its mime type. */
export function previewKindOf(url: string, mime = ''): PreviewKind {
  const type = (mime || '').toLowerCase()
  const ext = (/\.([a-z0-9]+)$/i.exec(url.split(/[?#]/)[0] ?? '')?.[1] ?? '').toLowerCase()
  if (type.startsWith('image/') || IMAGE.has(ext)) return 'image'
  if (type.startsWith('video/') || VIDEO.has(ext)) return 'video'
  if (type.startsWith('audio/') || AUDIO.has(ext)) return 'audio'
  if (type === 'application/pdf' || ext === 'pdf') return 'pdf'
  return 'url'
}

/** The file's name as the URL carries it. */
export function nameOfUrl(url: string): string {
  const tail = (url.split(/[?#]/)[0] ?? '').split('/').pop() ?? ''
  try {
    return decodeURIComponent(tail) || 'file'
  } catch {
    return tail || 'file'
  }
}

/**
 * Keep a file the app is showing: read it into memory and save it under its own name.
 * A relay link answers with CORS for this (the desktop's origin is a random loopback
 * port), so the browser saves quietly; where reading is refused, the system browser is
 * handed the link and the person saves it there.
 */
export async function saveMedia(url: string, name?: string): Promise<void> {
  try {
    const res = await fetch(url, { credentials: 'omit' })
    if (!res.ok) throw new Error(`${res.status}`)
    const blob = await res.blob()
    const href = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = name || nameOfUrl(url)
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(href), 30_000)
  } catch {
    window.open(url, '_blank', 'noopener')
  }
}
