/**
 * A file on this computer, served to the side pane: the agent made a clip, drew a
 * picture or wrote a PDF and `preview_show` puts it beside the conversation. The
 * pane is a browser, so the file has to come over HTTP — this route serves one
 * path at a time, with the content type the extension promises, and byte ranges
 * so a video can be scrubbed instead of loaded whole.
 *
 * Only a regular file of a kind the pane can show is served; a directory, a
 * missing path, an unknown extension or a path outside the person's own machine
 * is 404. The route goes through `mountGuarded` (`admit.ts`), so the harness's
 * host and session fence applies and another page on the loopback port cannot
 * read the disk.
 */
import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { homedir } from 'node:os'
import { basename, extname, join, resolve } from 'node:path'
import { PREVIEW_FILE_PREFIX } from './preview-kind.ts'

export { PREVIEW_FILE_PREFIX } from './preview-kind.ts'

/** What the pane can play, look at, or read as words, by extension. */
const TYPES: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.m4v': 'video/mp4',
  '.webm': 'video/webm',
  '.mov': 'video/quicktime',
  '.mp3': 'audio/mpeg',
  '.m4a': 'audio/mp4',
  '.wav': 'audio/wav',
  '.ogg': 'audio/ogg',
  '.oga': 'audio/ogg',
  '.opus': 'audio/ogg',
  '.flac': 'audio/flac',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.pdf': 'application/pdf',
  '.txt': 'text/plain; charset=utf-8',
  '.md': 'text/plain; charset=utf-8',
}

/** A path the agent gave: `~/…` means the person's home, everything else is taken as written. */
export function resolveUserPath(path: string): string {
  const trimmed = path.trim()
  return resolve(trimmed === '~' ? homedir() : trimmed.startsWith('~/') ? join(homedir(), trimmed.slice(2)) : trimmed)
}

export interface ByteRange {
  start: number
  end: number
}

/**
 * One `Range: bytes=…` header against a file of `size` bytes: undefined for the
 * whole file, `'invalid'` when the browser asked for something that cannot be
 * served (a multi-range request, a start past the end), a range otherwise.
 */
export function rangeOf(header: string | undefined, size: number): ByteRange | 'invalid' | undefined {
  if (!header) return undefined
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim())
  if (!match) return 'invalid'
  const [, first, last] = match
  if (first === '' && last === '') return 'invalid'
  if (first === '') {
    const suffix = Number(last)
    if (!Number.isFinite(suffix) || suffix <= 0) return 'invalid'
    return { start: Math.max(0, size - suffix), end: size - 1 }
  }
  const start = Number(first)
  const end = last === '' ? size - 1 : Number(last)
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end || start >= size) return 'invalid'
  return { start, end: Math.min(end, size - 1) }
}

function notFound(res: ServerResponse): void {
  res.writeHead(404, { 'cache-control': 'no-store' }).end()
}

/** `GET /nanomuse/file?path=<absolute path>`: one file, with ranges for a video. */
export async function servePreviewFile(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1')
  const given = url.searchParams.get('path') ?? ''
  if ((req.method !== 'GET' && req.method !== 'HEAD') || !given) return notFound(res)
  const path = resolveUserPath(given)
  const type = TYPES[extname(path).toLowerCase()]
  if (!type) return notFound(res)
  let size: number
  try {
    const info = await stat(path)
    if (!info.isFile()) return notFound(res)
    size = info.size
  } catch {
    return notFound(res)
  }
  const headers: Record<string, string | number> = {
    'content-type': type,
    'accept-ranges': 'bytes',
    'cache-control': 'no-store',
    'content-disposition': `inline; filename*=UTF-8''${encodeURIComponent(basename(path))}`,
  }
  const range = rangeOf(req.headers.range, size)
  if (range === 'invalid') {
    res.writeHead(416, { ...headers, 'content-range': `bytes */${size}` }).end()
    return
  }
  if (range) {
    res.writeHead(206, { ...headers, 'content-range': `bytes ${range.start}-${range.end}/${size}`, 'content-length': range.end - range.start + 1 })
    if (req.method === 'HEAD') {
      res.end()
      return
    }
    createReadStream(path, { start: range.start, end: range.end }).pipe(res)
    return
  }
  res.writeHead(200, { ...headers, 'content-length': size })
  if (req.method === 'HEAD') {
    res.end()
    return
  }
  createReadStream(path).pipe(res)
}
