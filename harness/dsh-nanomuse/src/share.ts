/**
 * Local files in an answer become S3 links (reg.ru Object Storage).
 *
 * The agent writes things like `[clip.mp4](</Volumes/.../clip.mp4>)`. A phone or the
 * web client cannot read that path. Before the answer goes back over the hub, every
 * absolute path that is a real file is uploaded to the private `nanolika` bucket and
 * the link is replaced with a presigned GET URL (24 hours). Files stay in the bucket
 * for 30 days, where the bucket lifecycle removes them.
 *
 * Credentials are never in this source: `s3.json` holds the endpoint, region and bucket,
 * while the access key and secret are read from the macOS Keychain (`nanolika-s3-access`
 * and `nanolika-s3-secret`). On a machine without those, the same fields may be put in
 * `s3.json` itself; that file must be mode 0600.
 */
import { execFileSync } from 'node:child_process'
import { createHash, createHmac, randomUUID } from 'node:crypto'
import { createReadStream, readFileSync, statSync } from 'node:fs'
import { request as httpsRequest } from 'node:https'
import { homedir } from 'node:os'
import { basename, extname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { statePath } from './paths.ts'

export interface S3Config {
  endpoint: string
  region: string
  bucket: string
  accessKeyId: string
  secretAccessKey: string
}

const DEFAULT_ENDPOINT = 'https://s3.regru.cloud'
const DEFAULT_REGION = 'us-east-1'
const DEFAULT_BUCKET = 'nanolika'
const PRESIGN_SECONDS = 24 * 60 * 60

const MIME: Record<string, string> = {
  '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm',
  '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4', '.aac': 'audio/aac', '.wav': 'audio/wav', '.flac': 'audio/flac',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif',
  '.pdf': 'application/pdf', '.zip': 'application/zip', '.txt': 'text/plain', '.md': 'text/markdown',
  '.json': 'application/json', '.csv': 'text/csv',
}

let configCache: S3Config | null | undefined

function keychain(service: string): string {
  try {
    return execFileSync('/usr/bin/security', ['find-generic-password', '-s', service, '-w'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    return ''
  }
}

/** The config, or null when this machine has no S3 setup. Cached after the first read. */
export function loadS3Config(): S3Config | null {
  if (configCache !== undefined) return configCache
  let file: Record<string, unknown> = {}
  try {
    const raw = JSON.parse(readFileSync(statePath('s3.json'), 'utf8')) as unknown
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) file = raw as Record<string, unknown>
  } catch {
    // absent or unreadable: the defaults and the Keychain are enough on this Mac
  }
  const str = (key: string, fallback = ''): string => {
    const value = file[key]
    return typeof value === 'string' && value.trim() ? value.trim() : fallback
  }
  const accessKeyId = str('accessKeyId') || keychain('nanolika-s3-access')
  const secretAccessKey = str('secretAccessKey') || keychain('nanolika-s3-secret')
  if (!accessKeyId || !secretAccessKey) {
    configCache = null
    return null
  }
  configCache = {
    endpoint: str('endpoint', DEFAULT_ENDPOINT).replace(/\/+$/, ''),
    region: str('region', DEFAULT_REGION),
    bucket: str('bucket', DEFAULT_BUCKET),
    accessKeyId,
    secretAccessKey,
  }
  return configCache
}

function hash(value: string | Buffer): string {
  return createHash('sha256').update(value).digest('hex')
}

function hmac(key: string | Buffer, value: string): Buffer {
  return createHmac('sha256', key).update(value).digest()
}

function signingKey(secret: string, date: string, region: string): Buffer {
  return hmac(hmac(hmac(hmac(`AWS4${secret}`, date), region), 's3'), 'aws4_request')
}

/** AWS wants every byte outside A-Z a-z 0-9 - _ . ~ escaped, unlike encodeURIComponent. */
function encode(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)
}

function encodePath(value: string): string {
  return value.split('/').map(encode).join('/')
}

function shanghaiDate(date = new Date()): { amz: string; day: string } {
  const iso = date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')
  return { amz: iso, day: iso.slice(0, 8) }
}

function hostOf(endpoint: string): string {
  return new URL(endpoint).host
}

/** A presigned GET, valid for 24 hours; Content-Disposition makes the browser download it. */
export function presignGet(cfg: S3Config, key: string, name: string, seconds = PRESIGN_SECONDS): string {
  const { amz, day } = shanghaiDate()
  const scope = `${day}/${cfg.region}/s3/aws4_request`
  const path = `${new URL(cfg.endpoint).pathname.replace(/\/+$/, '')}/${encode(cfg.bucket)}/${encodePath(key)}`
  const params: Record<string, string> = {
    'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
    'X-Amz-Credential': `${cfg.accessKeyId}/${scope}`,
    'X-Amz-Date': amz,
    'X-Amz-Expires': String(Math.max(1, Math.min(seconds, 7 * 24 * 60 * 60))),
    'X-Amz-SignedHeaders': 'host',
    'response-content-disposition': `attachment; filename="${name.replace(/["\\\r\n]/g, '_')}"`,
  }
  const query = Object.keys(params)
    .sort()
    .map((k) => `${encode(k)}=${encode(params[k] ?? '')}`)
    .join('&')
  const canonical = `GET\n${path}\n${query}\nhost:${hostOf(cfg.endpoint)}\n\nhost\nUNSIGNED-PAYLOAD`
  const toSign = `AWS4-HMAC-SHA256\n${amz}\n${scope}\n${hash(canonical)}`
  const signature = hmac(signingKey(cfg.secretAccessKey, day, cfg.region), toSign).toString('hex')
  return `${cfg.endpoint}${path}?${query}&X-Amz-Signature=${signature}`
}

function contentType(path: string): string {
  return MIME[extname(path).toLowerCase()] || 'application/octet-stream'
}

async function upload(cfg: S3Config, local: string, key: string): Promise<void> {
  const { amz, day } = shanghaiDate()
  const scope = `${day}/${cfg.region}/s3/aws4_request`
  const size = statSync(local).size
  const digest = await new Promise<string>((resolve, reject) => {
    const h = createHash('sha256')
    createReadStream(local)
      .on('data', (chunk) => h.update(chunk))
      .on('error', reject)
      .on('end', () => resolve(h.digest('hex')))
  })
  const path = `${new URL(cfg.endpoint).pathname.replace(/\/+$/, '')}/${encode(cfg.bucket)}/${encodePath(key)}`
  const type = contentType(local)
  const headers = {
    host: hostOf(cfg.endpoint),
    'content-type': type,
    'x-amz-content-sha256': digest,
    'x-amz-date': amz,
  }
  const signed = Object.keys(headers).sort().join(';')
  const canonicalHeaders = Object.keys(headers).sort().map((k) => `${k}:${headers[k as keyof typeof headers]}\n`).join('')
  const canonical = `PUT\n${path}\n\n${canonicalHeaders}\n${signed}\n${digest}`
  const toSign = `AWS4-HMAC-SHA256\n${amz}\n${scope}\n${hash(canonical)}`
  const signature = hmac(signingKey(cfg.secretAccessKey, day, cfg.region), toSign).toString('hex')
  const authorization = `AWS4-HMAC-SHA256 Credential=${cfg.accessKeyId}/${scope}, SignedHeaders=${signed}, Signature=${signature}`
  await new Promise<void>((resolve, reject) => {
    const req = httpsRequest(
      new URL(`${cfg.endpoint}${path}`),
      { method: 'PUT', headers: { ...headers, 'content-length': String(size), authorization } },
      (res) => {
        const chunks: Buffer[] = []
        res.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
        res.on('end', () => {
          if ((res.statusCode || 500) >= 300) reject(new Error(`S3 PUT ${res.statusCode}: ${Buffer.concat(chunks).toString().slice(0, 200)}`))
          else resolve()
        })
      },
    )
    req.on('error', reject)
    createReadStream(local).pipe(req)
  })
}

export interface LocalLink {
  label: string
  path: string
}

/** The absolute local files an answer links to, deduplicated by path. */
export function localFileLinks(text: string): LocalLink[] {
  const out: LocalLink[] = []
  const seen = new Set<string>()
  const re = /\[([^\]\n]+)\]\((?:<([^>\n]+)>|([^()\s]+))\)/g
  let match: RegExpExecArray | null
  while ((match = re.exec(text)) !== null) {
    const raw = (match[2] || match[3] || '').trim()
    let path = raw
    if (raw.startsWith('file://')) {
      try { path = fileURLToPath(raw) } catch { continue }
    } else if (raw.startsWith('~/')) {
      path = `${homedir()}/${raw.slice(2)}`
    } else if (!raw.startsWith('/')) {
      continue
    }
    try {
      if (!statSync(path).isFile()) continue
    } catch {
      continue
    }
    if (seen.has(path)) continue
    seen.add(path)
    out.push({ label: match[1] ?? '', path })
  }
  return out
}

/** Rewrite local links to presigned S3 URLs; anything that fails stays a readable label. */
export async function shareFileLinks(text: string, cfg = loadS3Config()): Promise<string> {
  if (!cfg) return text
  const links = localFileLinks(text)
  if (!links.length) return text
  let changed = false
  let failed = false
  const replacements = new Map<string, string>()
  for (const link of links) {
    try {
      const key = `files/${randomUUID()}/${basename(link.path).replace(/[^\w.\- ()\u4e00-\u9fff]+/g, '_').slice(0, 120) || 'file'}`
      await upload(cfg, link.path, key)
      replacements.set(link.path, presignGet(cfg, key, basename(link.path)))
      changed = true
    } catch {
      failed = true
    }
  }
  if (!changed) return text
  let out = text.replace(/\[([^\]\n]+)\]\((?:<([^>\n]+)>|([^()\s]+))\)/g, (whole, label: string, angle?: string, plain?: string) => {
    const raw = (angle || plain || '').trim()
    let path = raw
    if (raw.startsWith('file://')) { try { path = fileURLToPath(raw) } catch { return whole } }
    else if (raw.startsWith('~/')) path = `${homedir()}/${raw.slice(2)}`
    const url = replacements.get(path)
    return url ? `[${label}](${url})` : whole
  })
  if (changed) {
    const note = /[А-Яа-яЁё]/.test(out)
      ? 'Ссылки на файлы действуют 24 часа; файлы хранятся 30 дней.'
      : 'File links are valid for 24 hours; files are kept for 30 days.'
    out = `${out.trimEnd()}\n\n_${note}_`
  }
  return out
}
