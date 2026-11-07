/**
 * Mailboxes for Settings → Connectors: the runtime owns `app-settings.json` and the vault,
 * the app only runs `nanomuse mail …`. Several mailboxes live side by side, each with its
 * own app password: the password goes in on stdin and never comes back out.
 */
import { spawn } from 'node:child_process'
import { runtimeInfo } from './hands-check.ts'

export interface Mailbox {
  id: string
  label: string
  address: string
  imap_host: string
  imap_port: number
  smtp_host: string
  smtp_port: number
  smtp_starttls: boolean
  enabled: boolean
}

export interface MailTest {
  ok: boolean
  id?: string
  address?: string
  inbox?: number | null
  error?: string
}

interface Run {
  code: number
  stdout: string
  stderr: string
}

/** What the runtime said went wrong, with its own code, so the screen can say more. */
export class MailError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'MailError'
  }
}

/** IMAP and SMTP both time out at fifteen seconds each; half a minute more for the start. */
const TIMEOUT_MS = 75_000

function lastJsonLine(text: string): string {
  return text.trim().split('\n').filter(Boolean).pop() ?? ''
}

/**
 * A failed run, read from the JSON the runtime prints with `--json` (`{ok: false, code,
 * message}`), and from plain text otherwise. Exit codes are not enough: "already
 * connected" and "no such mailbox" need different words on screen.
 */
function failure(result: Run, fallback: string): MailError {
  const line = lastJsonLine(result.stdout)
  if (line.startsWith('{')) {
    try {
      const parsed = JSON.parse(line) as { code?: unknown; message?: unknown; error?: unknown }
      const code = typeof parsed.code === 'string' && parsed.code ? parsed.code : 'failed'
      const text =
        typeof parsed.message === 'string' && parsed.message
          ? parsed.message
          : typeof parsed.error === 'string' && parsed.error
            ? parsed.error
            : fallback
      return new MailError(code, text)
    } catch {
      // not the shape of an error: fall through to the plain text
    }
  }
  // An installed build older than the mail commands answers with the runtime's own usage
  // block ("Usage: nanomuse [OPTIONS] COMMAND … No such command 'mail'"). That is not a
  // sentence for a person, and nothing on the screen can fix it: say a build is needed.
  const said = `${result.stdout}\n${result.stderr}`
  if (/No such command ['"]?mail\b/i.test(said)) {
    return new MailError('runtime_old', 'this build of the app has no mail commands yet')
  }
  return new MailError('failed', result.stderr.trim() || result.stdout.trim() || fallback)
}

async function run(args: string[], stdin?: string): Promise<Run> {
  const info = await runtimeInfo()
  if (!info.ok) {
    throw new Error(
      info.problem === 'not-found'
        ? 'the runtime is not installed or is not on PATH'
        : 'the runtime cannot be executed',
    )
  }
  return await new Promise<Run>((resolve, reject) => {
    const child = spawn(info.path, args, { stdio: ['pipe', 'pipe', 'pipe'], env: process.env })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill()
      reject(new Error('the runtime did not answer in time'))
    }, TIMEOUT_MS)
    child.stdout.on('data', (chunk) => {
      stdout += String(chunk)
    })
    child.stderr.on('data', (chunk) => {
      stderr += String(chunk)
    })
    child.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ code: code ?? 1, stdout, stderr })
    })
    child.stdin.end(stdin ?? '')
  })
}

function parseJson<T>(result: Run): T {
  const line = result.stdout.trim().split('\n').filter(Boolean).pop() ?? ''
  try {
    return JSON.parse(line) as T
  } catch {
    throw new Error(result.stderr.trim() || 'the runtime answered nothing')
  }
}

export async function listMailboxes(): Promise<Mailbox[]> {
  const result = await run(['mail', 'list', '--json'])
  if (result.code !== 0) throw failure(result, 'mail list failed')
  return parseJson<Mailbox[]>(result)
}

export interface NewMailbox {
  address: string
  label?: string
  password: string
  imap_host?: string
  imap_port?: number
  smtp_host?: string
  smtp_port?: number
}

export async function addMailbox(input: NewMailbox): Promise<Mailbox> {
  const args = ['mail', 'add', input.address.trim(), '--json', '--password-stdin']
  if (input.label?.trim()) args.push('--label', input.label.trim())
  if (input.imap_host?.trim()) args.push('--imap-host', input.imap_host.trim())
  if (input.imap_port) args.push('--imap-port', String(input.imap_port))
  if (input.smtp_host?.trim()) args.push('--smtp-host', input.smtp_host.trim())
  if (input.smtp_port) args.push('--smtp-port', String(input.smtp_port))
  const result = await run(args, `${input.password}\n`)
  if (result.code !== 0) throw failure(result, 'mail add failed')
  return parseJson<Mailbox>(result)
}

export interface MailboxEdit {
  id: string
  address?: string
  label?: string
  password?: string
  imap_host?: string
  imap_port?: number
  smtp_host?: string
  smtp_port?: number
  enabled?: boolean
}

export async function updateMailbox(input: MailboxEdit): Promise<Mailbox> {
  const args = ['mail', 'edit', input.id, '--json']
  if (input.address?.trim()) args.push('--address', input.address.trim())
  if (input.label !== undefined) args.push('--label', input.label.trim())
  if (input.password) args.push('--password-stdin')
  if (input.imap_host?.trim()) args.push('--imap-host', input.imap_host.trim())
  if (input.imap_port) args.push('--imap-port', String(input.imap_port))
  if (input.smtp_host?.trim()) args.push('--smtp-host', input.smtp_host.trim())
  if (input.smtp_port) args.push('--smtp-port', String(input.smtp_port))
  if (input.enabled === true) args.push('--enable')
  if (input.enabled === false) args.push('--disable')
  const result = await run(args, input.password ? `${input.password}\n` : '')
  if (result.code !== 0) throw failure(result, 'mail edit failed')
  return parseJson<Mailbox>(result)
}

export async function removeMailbox(id: string): Promise<void> {
  const result = await run(['mail', 'remove', id])
  if (result.code !== 0) throw failure(result, 'mail remove failed')
}

export async function testMailbox(id?: string): Promise<MailTest> {
  const args = ['mail', 'test']
  if (id) args.push(id)
  args.push('--json')
  const result = await run(args)
  try {
    return JSON.parse(result.stdout.trim().split('\n').filter(Boolean).pop() ?? '{}') as MailTest
  } catch {
    return { ok: false, error: result.stderr.trim() || 'no answer' }
  }
}
