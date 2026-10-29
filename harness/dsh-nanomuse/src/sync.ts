/**
 * Conversations synced between the account's devices (contracts C7 and C8), the
 * desktop's half. The relay keeps the text of every conversation under `/v1/sync/*`;
 * this computer pushes the person's words the moment they are sent and the model's
 * final text when the turn ends, and pulls what the other devices pushed — on start,
 * on the hub's `sync` frame and every minute.
 *
 * **One thread** (C8). A conversation written on another device is not a mirror
 * beside the chats here: it *is* a chat here. The account's one main conversation
 * lives in the main chat of this computer (its id adopted from the relay when the
 * relay has one), and a side conversation pulled from the relay gets a dsh session
 * at once, with its title, so it continues here like any other chat and its turns
 * go up under the same conversation id. The turns written elsewhere are *kept* by the
 * host in a store of its own (`$DSH_HOME/nanomuse/sync-remote.json`) — never written
 * into the session's log: a `user/message` appended there outside a turn makes the log
 * unreadable once a turn runs, and the chat does not render such a row anyway — and the
 * browser half (`client/RemoteBubbles.ts`) shows them as the other device's bubbles,
 * "From Pixel 8" under each, placed by their time among the local turns. The model
 * hears them as context for its next step (`agent.inject`, a note per pull: "Meanwhile,
 * in this same conversation on another device…"), model-facing, never the person's
 * words, so never pushed back; a row from this device coming back on a pull is known
 * by its device and never kept twice.
 *
 * On sign-in and when the switch goes on the pull comes first (the relay's history
 * reaches the sessions here), then the whole eligible history of this computer goes
 * up, oldest first, 200 messages a POST.
 *
 * **Main first** (C9). Only the main conversation syncs unless this device's "Also sync
 * side chats" is on (`state.sideChats`): off, side chats are neither pushed nor pulled
 * (`scope=main`), and a side row that still arrives makes no session. The first pull of an
 * account asks for the newest 300 messages (`tail=300`); the switch going on pulls the
 * account's side chats the same way, once. While a turn runs here on a synced conversation
 * the relay is told (`POST /v1/sync/working`, best-effort), and the other devices' `working`
 * frames put a quiet "kwai is working…" line under their last prompt here (`workingOf`).
 *
 * **Whose conversations** (C10). Every mapping entry carries the `owner` — the `account.id` of
 * the account the session was first pushed to or pulled from; a conversation written while
 * signed out and never synced has none. Signed in as B, the column shows B's and the
 * ownerless (`view().foreign` names the rest, hidden, not deleted), the push loop takes B's
 * and the ownerless — which become B's on their first push — and never A's; a different
 * `account.id` than the last one starts the pull over (cursor 0) and drops what was pulled
 * for A (the relay's rows, the kept turns, presence), keeping A's mapping so switching back
 * works. Signed out, everything local is shown and nothing moves.
 *
 * **Whose cloud** (C11). A chat is in the cloud exactly when it has a conversation in the account,
 * and the three cases differ in who may change that. The main chat *is* the account's own
 * conversation: always in the cloud, never taken out (`setSynced` answers `main`). A chat that came
 * by sync, whether from another device, from a messenger or from an account that already held it,
 * lives in the cloud: it cannot be made local (`synced-from-cloud`), only deleted from the account
 * whole (`deleteConversation`, which the browser half confirms first), the copy here staying on as a
 * local chat. A chat created here is local until the person puts a cloud on it, and can be taken out
 * again; taking it out deletes its conversation on the relay, since the messengers and the browser
 * see only what the relay holds, and leaves the copy here. `clouds` says which of the three a
 * session is, `applyConversation` marks what arrives, and `local` sticks against the older
 * device-wide "Also sync side chats" switch.
 *
 * Never a key in a log, never a file: attachments travel as names and sizes.
 */
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { mkdir, rename, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'
import { RELAY_TIMEOUT_MS, RelayError, type TimedFetch, withTimeout } from './relay.ts'

declare module '@deepseek-ai/dsh-llm' {
  interface MessageSourceMap {
    /** A turn of this conversation written on another device of the account (C8). */
    'nanomuse-sync': { kind: 'nanomuse-sync'; role: 'user' | 'assistant'; mid: string; device: string; deviceName: string; at: number }
  }
}

/** The relay's limits (contract C7). */
export const MAX_POST_MESSAGES = 200
export const PUSH_DELAY_MS = 2000
/** The person's words go up this soon after they are sent (C8). */
export const SEND_DELAY_MS = 50
export const PULL_EVERY_MS = 60_000
export const PAGE = 500

const JSON_HEADERS = { 'content-type': 'application/json' }

export interface SyncRelayState {
  enabled: boolean
  cursor: number
  counts: { conversations: number; messages: number }
  /** Devices working on a conversation right now (C9), unexpired. */
  working: WireWorking[]
}

/** The hub's `working` frame and the rows of `/v1/sync/state`'s `working` (C9). */
export interface WireWorking {
  cid: string
  from: string
  device_name: string
  working: boolean
  /** Unix seconds, the relay's clock. */
  at: number
}

/** The newest messages a first pull asks for (C9): `since=0&tail=300`. */
export const TAIL = 300
/** A `working` presence is over this long after its `at` (C9). */
export const WORKING_TTL_MS = 10 * 60_000
/** The hub's `sync` frames arriving together become one pull. */
export const FRAME_COALESCE_MS = 1000

export interface WireConversation {
  cid: string
  kind: 'main' | 'side'
  title: string
  device: string
  device_name: string
  created_at: number
  updated_at: number
  deleted: boolean
  archived: boolean
  seq: number
}

export interface WireMessage {
  mid: string
  cid: string
  seq: number
  device: string
  device_name: string
  role: 'user' | 'assistant'
  text: string
  truncated: boolean
  created_at: number
  deleted: boolean
}

export interface SyncChanges {
  cursor: number
  more: boolean
  conversations: WireConversation[]
  messages: WireMessage[]
}

export interface PushResult {
  cursor: number
  accepted: number
  rejected: Array<{ cid?: string; mid?: string; reason: string; cid_main?: string }>
}

/** `/v1/sync/*` as the engine needs them; `fetchImpl` is swapped in tests. Every call has a deadline (relay.ts `withTimeout`), so a hung connection does not stall the sync for good. */
export class SyncRelay {
  readonly origin: string
  private readonly fetchImpl: TimedFetch

  constructor(origin: string, fetchImpl: typeof fetch = fetch, timeoutMs = RELAY_TIMEOUT_MS) {
    this.origin = origin.replace(/\/+$/, '')
    this.fetchImpl = withTimeout(fetchImpl, timeoutMs)
  }

  async state(apiKey: string): Promise<SyncRelayState> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/state`, { headers: this.auth(apiKey) })
    if (!res.ok) await fail(res)
    return toState(await res.json())
  }

  async setArchived(apiKey: string, cid: string, archived: boolean): Promise<void> {
    const res = await this.fetchImpl(this.origin + '/v1/sync/conversations/' + encodeURIComponent(cid) + '/archive', { method: 'POST', headers: { ...this.auth(apiKey), ...JSON_HEADERS }, body: JSON.stringify({ archived }) })
    if (!res.ok) await fail(res)
  }

  async setEnabled(apiKey: string, enabled: boolean): Promise<SyncRelayState> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/state`, { method: 'PUT', headers: { ...this.auth(apiKey), ...JSON_HEADERS }, body: JSON.stringify({ enabled }) })
    if (!res.ok) await fail(res)
    return toState(await res.json())
  }

  /**
   * The changes since a cursor (C7); `scope: 'main'` asks for the main conversation only, `tail`
   * (with `since=0`) for the newest `tail` messages of the scope and their conversations' rows (C9).
   */
  async changes(apiKey: string, since: number, limit = PAGE, scope: 'all' | 'main' = 'all', tail = 0): Promise<SyncChanges> {
    const query = `since=${since}&limit=${limit}${scope === 'main' ? '&scope=main' : ''}${tail > 0 && since === 0 ? `&tail=${tail}` : ''}`
    const res = await this.fetchImpl(`${this.origin}/v1/sync/changes?${query}`, { headers: this.auth(apiKey) })
    if (!res.ok) await fail(res)
    const body = (await res.json()) as Record<string, unknown>
    return {
      cursor: Number(body.cursor ?? since),
      more: body.more === true,
      conversations: Array.isArray(body.conversations) ? body.conversations.map(toConversation) : [],
      messages: Array.isArray(body.messages) ? body.messages.map(toMessage) : [],
    }
  }

  async catalog(apiKey: string, client: string, device: string): Promise<{ account_id?: string; cursor: number; complete: boolean; deleted?: WireConversation[]; conversations: Array<WireConversation & { unread: number }> }> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/catalog?${new URLSearchParams({ client, device })}`, { headers: this.auth(apiKey) })
    if (!res.ok) await fail(res)
    const data = await res.json() as { account_id?: string; cursor: number; complete: boolean; deleted?: WireConversation[]; conversations: Array<WireConversation & { unread: number }> }
    if (data.complete !== true || !Array.isArray(data.conversations)) throw new Error('incomplete chat catalogue')
    return data
  }

  async chatMessages(apiKey: string, cid: string, after: number): Promise<{ account_id?: string; cursor: number; through_seq: number; more: boolean; messages: WireMessage[] }> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/conversations/${encodeURIComponent(cid)}/messages?after=${after}`, { headers: this.auth(apiKey) })
    if (!res.ok) await fail(res)
    return await res.json() as { account_id?: string; cursor: number; through_seq: number; more: boolean; messages: WireMessage[] }
  }

  async markRead(apiKey: string, cid: string, client: string, through_seq: number): Promise<void> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/conversations/${encodeURIComponent(cid)}/read`, { method: 'POST', headers: { ...this.auth(apiKey), ...JSON_HEADERS }, body: JSON.stringify({ client, through_seq }) })
    if (!res.ok) await fail(res)
  }

  /** Presence (C9): this device started or finished a turn on a conversation. Best-effort: the caller never retries. */
  async working(apiKey: string, device: string, cid: string, working: boolean): Promise<void> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/working`, {
      method: 'POST',
      headers: { ...this.auth(apiKey), ...JSON_HEADERS },
      body: JSON.stringify({ cid, working, device }),
    })
    if (!res.ok) await fail(res)
  }

  async push(apiKey: string, device: string, conversations: Record<string, unknown>[], messages: Record<string, unknown>[]): Promise<PushResult> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/changes`, {
      method: 'POST',
      headers: { ...this.auth(apiKey), ...JSON_HEADERS },
      body: JSON.stringify({ device, conversations, messages }),
    })
    if (!res.ok) await fail(res)
    const body = (await res.json()) as Record<string, unknown>
    return {
      cursor: Number(body.cursor ?? 0),
      accepted: Number(body.accepted ?? 0),
      rejected: Array.isArray(body.rejected) ? (body.rejected as PushResult['rejected']) : [],
    }
  }

  async deleteConversation(apiKey: string, cid: string, device: string): Promise<void> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/conversations/${encodeURIComponent(cid)}`, { method: 'DELETE', headers: { ...this.auth(apiKey), 'x-nanomuse-device': device } })
    if (!res.ok && res.status !== 404) await fail(res)
  }

  /** "Delete synced conversations": the store emptied, the switch kept. */
  async wipe(apiKey: string): Promise<SyncRelayState> {
    const res = await this.fetchImpl(`${this.origin}/v1/sync/changes`, { method: 'DELETE', headers: this.auth(apiKey) })
    if (!res.ok) await fail(res)
    return toState(await res.json())
  }

  private auth(apiKey: string): Record<string, string> {
    return { authorization: `Bearer ${apiKey}` }
  }
}

async function fail(res: Response): Promise<never> {
  let code = `http_${res.status}`
  let message = `${res.status} ${res.statusText}`.trim()
  try {
    const body = (await res.json()) as { error?: { code?: string; message?: string } }
    if (body.error?.code) code = body.error.code
    if (body.error?.message) message = body.error.message
  } catch {
    // not JSON — the status line is the message
  }
  throw new RelayError(res.status, code, message)
}

function toState(value: unknown): SyncRelayState {
  const b = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  const counts = (b.counts && typeof b.counts === 'object' ? b.counts : {}) as Record<string, unknown>
  return {
    enabled: b.enabled !== false,
    cursor: Number(b.cursor ?? 0),
    counts: { conversations: Number(counts.conversations ?? 0), messages: Number(counts.messages ?? 0) },
    working: Array.isArray(b.working) ? b.working.map((w) => toWorking(w, true)) : [],
  }
}

export function toWorking(value: unknown, working?: boolean): WireWorking {
  const w = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return { cid: String(w.cid ?? ''), from: String(w.from ?? ''), device_name: String(w.device_name ?? ''), working: working ?? w.working === true, at: Number(w.at ?? 0) }
}

function toConversation(value: unknown): WireConversation {
  const c = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return {
    cid: String(c.cid ?? ''),
    kind: c.kind === 'main' ? 'main' : 'side',
    title: String(c.title ?? ''),
    device: String(c.device ?? ''),
    device_name: String(c.device_name ?? ''),
    created_at: Number(c.created_at ?? 0),
    updated_at: Number(c.updated_at ?? 0),
    deleted: c.deleted === true,
    archived: c.archived === true,
    seq: Number(c.seq ?? 0),
  }
}

function toMessage(value: unknown): WireMessage {
  const m = (value && typeof value === 'object' ? value : {}) as Record<string, unknown>
  return {
    mid: String(m.mid ?? ''),
    cid: String(m.cid ?? ''),
    seq: Number(m.seq ?? 0),
    device: String(m.device ?? ''),
    device_name: String(m.device_name ?? ''),
    role: m.role === 'assistant' ? 'assistant' : 'user',
    text: String(m.text ?? ''),
    truncated: m.truncated === true,
    created_at: Number(m.created_at ?? 0),
    deleted: m.deleted === true,
  }
}

// ---- the sessions, as the engine sees them -----------------------------------------------

/** One message of a dsh session the engine may sync: the person's prompt or the model's final text. */
export interface SessionLine {
  /** The dsh message id: stable, so a line is pushed once. */
  id: string
  role: 'user' | 'assistant'
  text: string
  /** Unix epoch milliseconds. */
  at: number
}

export interface SessionInfo {
  id: string
  title: string
  blank: boolean
  createdAt: number
  updatedAt: number
  archived?: boolean
}

/** A turn of the conversation written on another device, as it goes into a session's log here (C8). */
export interface RemoteLine {
  mid: string
  role: 'user' | 'assistant'
  text: string
  /** Unix epoch milliseconds (the relay's `created_at`, in ms). */
  at: number
  /** Local wall-clock milliseconds when this computer kept the row, so skewed device clocks still anchor subsequent local prompts. */
  keptAt?: number
  device: string
  deviceName: string
}

/** The slice of the session API the engine uses; `cloud.ts` binds it to `ctx.sessionController`, tests fake it. */
export interface SyncSessions {
  list(): Promise<SessionInfo[]>
  /** The session's human prompts and final assistant texts, in order; [] for a session that cannot be read. */
  lines(sessionId: string): Promise<SessionLine[]>
  create(title: string): Promise<string>
  rename(sessionId: string, title: string): Promise<void>
  /**
   * A turn from another device, kept by the host for the transcript — in a store of its own,
   * never in the session's log: a surface message appended there outside a turn makes the log
   * unreadable once a turn runs ("system/message requires a protected first surface head").
   */
  keep(sessionId: string, line: RemoteLine): Promise<void>
  /** A kept turn deleted elsewhere. */
  forget(sessionId: string, mid: string): Promise<void>
  /** Every kept turn dropped (C10: another account signed in). */
  forgetAll?(): Promise<void>
  /**
   * Put the first remote user line into an empty session as a real prompt, then stop its
   * turn. The harness lists a chat only after a prompt; a synced conversation must not wait
   * for the person to open a row that the column is hiding.
   */
  promote?(sessionId: string, line: RemoteLine, title: string): Promise<boolean>
  /** Model-facing context for the session's next step (`agent.inject`): what the other devices said; never shown as the person's words. */
  inject(sessionId: string, text: string): Promise<void>
  /** Persisted remote turns, replayed before a shared task after reconnect. */
  remote?(sessionId: string): RemoteLine[]
}

/**
 * The other devices' turns, kept by session for the transcript: one JSON file, read once,
 * written whole after each change (coalesced, atomically — a temp file renamed over). A
 * line is kept once (by `mid`), in time order.
 */
export class RemoteStore {
  private lines = new Map<string, RemoteLine[]>()
  private loaded = false
  private timer: ReturnType<typeof setTimeout> | undefined
  private writing: Promise<void> = Promise.resolve()

  constructor(
    private readonly path: string,
    private readonly onError: (error: unknown) => void = () => undefined,
  ) {}

  private load(): void {
    if (this.loaded) return
    this.loaded = true
    try {
      const raw = JSON.parse(readFileSync(this.path, 'utf8')) as Record<string, RemoteLine[]>
      for (const [sessionId, lines] of Object.entries(raw)) if (Array.isArray(lines)) this.lines.set(sessionId, lines)
    } catch {
      // no store yet
    }
  }

  linesOf(sessionId: string): RemoteLine[] {
    this.load()
    return [...(this.lines.get(sessionId) ?? [])]
  }

  add(sessionId: string, line: RemoteLine): void {
    this.load()
    const list = this.lines.get(sessionId) ?? []
    if (list.some((l) => l.mid === line.mid)) return
    list.push(line.keptAt !== undefined ? line : { ...line, keptAt: Date.now() })
    list.sort((a, b) => a.at - b.at)
    this.lines.set(sessionId, list)
    this.saveSoon()
  }

  remove(sessionId: string, mid: string): void {
    this.load()
    const list = this.lines.get(sessionId)
    if (!list) return
    const next = list.filter((l) => l.mid !== mid)
    if (next.length === list.length) return
    if (next.length === 0) this.lines.delete(sessionId)
    else this.lines.set(sessionId, next)
    this.saveSoon()
  }

  private saveSoon(): void {
    if (this.timer) return
    this.timer = setTimeout(() => {
      this.timer = undefined
      this.writing = this.writing.then(() => this.write()).catch(this.onError)
    }, 200)
    this.timer.unref?.()
  }

  private async write(): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true })
    const tmp = `${this.path}.${process.pid}.tmp`
    await writeFile(tmp, JSON.stringify(Object.fromEntries(this.lines)) + '\n', { mode: 0o600 })
    await rename(tmp, this.path)
  }

  /** Everything forgotten (C10: another account signed in; its turns are pulled afresh). */
  clear(): void {
    this.load()
    if (this.lines.size === 0) return
    this.lines.clear()
    this.saveSoon()
  }

  /** Written now (the host stopping). */
  async flush(): Promise<void> {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = undefined
      this.writing = this.writing.then(() => this.write()).catch(this.onError)
    }
    await this.writing
  }
}

/** The note that hands the model what the other devices added to a conversation that lives in a session here. */
export function meanwhileNote(lines: RemoteLine[]): string {
  const MAX_LINES = 40
  const MAX_CHARS = 12_000
  const recent = lines.slice(-MAX_LINES)
  const rows: string[] = []
  let size = 0
  for (const l of [...recent].reverse()) {
    const row = `${l.role === 'user' ? 'Person' : 'Muse'}${l.deviceName ? ` (on ${l.deviceName})` : ''}: ${l.text}`
    if (size + row.length > MAX_CHARS && rows.length > 0) break
    rows.unshift(row)
    size += row.length
  }
  const omitted = lines.length - rows.length
  return `[Meanwhile, in this same conversation on another device of the account${omitted > 0 ? ` (${omitted} earlier turn${omitted === 1 ? '' : 's'} not shown)` : ''}:]\n${rows.join('\n')}\n[End of synced history. These messages are part of THIS conversation and are visible to you above. Device names identify where the same person wrote them, not another chat. Use this history when answering. A message without a reply does not prove it was unseen: work on the other device may still be running or waiting for approval. Check the available status before explaining a delay; do not invent a delivery failure or an offline device. This history update alone does not request a second response.]`
}

// ---- state --------------------------------------------------------------------------------

/** A conversation as the relay lists it, kept so a late session, a rename or a tombstone finds it. */
export interface KnownConversation {
  kind: 'main' | 'side'
  title: string
  device: string
  deviceName: string
  /** Unix seconds, the relay's clock. */
  createdAt: number
  updatedAt: number
  archived?: boolean
}

/**
 * Where a chat's cloud stands (C11). `device`: the person turned the cloud on here. `sync`: the chat
 * arrived by sync, so it lives in the account and may only be deleted from it. `local`: the person
 * took the cloud off here, and the older "Also sync side chats" switch does not put it back. A
 * session with no entry is local.
 */
export type CloudMark = 'device' | 'sync' | 'local'

/** Why a cloud switch or a delete was refused (C11), the browser half's sentence to the person. */
export type CloudRefusal = 'main' | 'synced-from-cloud' | 'not-synced-account' | 'unknown-session'

/** The answer to a cloud switch or to deleting a chat from the account (C11): the whole view, and whether it was done. */
export interface SyncedOutcome extends SyncView {
  ok: boolean
  reason?: CloudRefusal
}

export interface SyncState {
  messageCursors?: Record<string, number>
  /** The last signed-in account (`/v1/me` → `account.id`, opaque); kept through a sign-out so a change is known (C10). */
  accountId: string
  cursor: number
  /** Per-scope cursors: a main-only pull must not move the all-scope cursor. */
  cursors?: { main: number; all: number }
  enabled: boolean
  /** The session that is the main chat here: the account's one main conversation. */
  mainSession: string
  /** "Also sync side chats" (C9), per device, off by default: off → only the main conversation goes up and comes down. */
  sideChats: boolean
  /** The cloud on each chat (C11), where it stands per session; absent = local. */
  pendingArchives?: Record<string, boolean>
  clouds: Record<string, CloudMark>
  /** dsh session id → conversation id. */
  cids: Record<string, string>
  /** dsh session id → the account it was first pushed to or pulled from (C10); absent = never synced. */
  owners: Record<string, string>
  /** dsh message id → the mid it was pushed as. */
  mids: Record<string, string>
  /** session id → the title last pushed (or pulled: not pushed back). */
  titles: Record<string, string>
  /** cid → what the relay says of the conversation. */
  conversations: Record<string, KnownConversation>
  /** mid → the session it was appended to: a row goes into a log once. */
  pulled: Record<string, string>
  /** Rows deleted elsewhere after they were appended here: a log forgets nothing, the browser half hides them. */
  hidden: string[]
  /** Sessions whose conversation was deleted elsewhere: the browser half archives them (the host has no archive) and reports back. */
  toRestore?: string[]
  toArchive: string[]
  /** 0.1.36 kept the other devices' chats as mirrors; a state that still has them is migrated (`migrate`). */
  mirrors?: Record<string, unknown>
}

export function emptyState(): SyncState {
  return { accountId: '', cursor: 0, cursors: { main: 0, all: 0 }, enabled: true, mainSession: '', sideChats: false, clouds: {}, cids: {}, owners: {}, mids: {}, titles: {}, conversations: {}, pulled: {}, hidden: [], toArchive: [] }
}

/**
 * A 0.1.36 state (mirrors, no `conversations`) starts the pull over from zero: what the
 * mirrors held becomes sessions and log rows on that pull; the ids of what this device
 * pushed stay, so nothing goes up twice, and our own rows coming back are known by device.
 * A 0.1.38 state (no `owners`) gives every mapped session to the account it was synced with
 * (C10): a mapping exists only because the session was pushed to or pulled from that account.
 * A 0.29 state kept the per-chat switch as `synced` (session id → on): a chat whose cloud the
 * person turned on there is `device`, and a chat with a conversation but no switch is `sync`,
 * since a conversation in the account is a chat in the cloud (C11).
 */
export function migrate(state: SyncState | undefined): SyncState {
  if (!state) return emptyState()
  const next: SyncState = { ...emptyState(), ...state, sideChats: state.sideChats === true, clouds: { ...(state.clouds ?? {}) }, owners: { ...(state.owners ?? {}) } }
  next.cursors = state.cursors ?? { main: 0, all: next.sideChats ? state.cursor : 0 }
  // 0.29's `synced` map under this state's own key, dropped once it has been read
  const legacy = (next as unknown as { synced?: Record<string, boolean> }).synced
  if (legacy && typeof legacy === 'object') {
    for (const [sessionId, on] of Object.entries(legacy)) if (on === true && next.clouds[sessionId] === undefined) next.clouds[sessionId] = 'device'
    delete (next as unknown as { synced?: unknown }).synced
  }
  for (const [sessionId, cid] of Object.entries(next.cids)) {
    if (sessionId === next.mainSession || next.clouds[sessionId] !== undefined) continue
    if (next.conversations[cid]) next.clouds[sessionId] = 'sync'
  }
  if ('mirrors' in next || !state.conversations) {
    delete next.mirrors
    next.cursor = 0
    next.conversations = {}
    next.pulled = {}
    next.hidden = []
    next.toArchive = []
  }
  if (!state.owners && state.accountId) {
    for (const sessionId of Object.keys(next.cids)) next.owners[sessionId] = state.accountId
  }
  return next
}

/**
 * The account-scoped part of a state, started over for another account (C10, rule 4): the
 * cursor at 0 (a fresh `tail=300` pull), the relay's rows and what was pulled forgotten, the
 * mapping — every account's — kept; a main chat that belongs to another account is no
 * longer the main chat here.
 */
export function forAccount(state: SyncState, accountId: string): SyncState {
  const next: SyncState = { ...state, accountId, cursor: 0, cursors: { main: 0, all: 0 }, conversations: {}, pulled: {}, hidden: [], toArchive: [], pendingArchives: {} }
  const mainOwner = state.owners[state.mainSession]
  if (mainOwner && mainOwner !== accountId) next.mainSession = ''
  return next
}

export interface SyncEngineOptions {
  relay: SyncRelay
  sessions: SyncSessions
  /** The account key when signed in; nothing when not. */
  token(): Promise<string | undefined>
  deviceId(): string
  /** Sessions that are another device's tasks (`task.ts`) are not synced: they are that device's conversation. */
  isTaskSession?(sessionId: string): boolean
  load(): SyncState | undefined
  save(state: SyncState): Promise<void>
  /** The view changed: tell the browser half. */
  onChange?(): void
  log?(level: 'info' | 'warn' | 'debug', text: string): void
  pushDelayMs?: number
  pullEveryMs?: number
  frameCoalesceMs?: number
}

/** What the browser half reads: Data controls, the chats column and the bubbles from elsewhere. */
export interface SyncView {
  catalogue: boolean
  unread: Record<string, number>
  enabled: boolean
  /** "Also sync side chats" (C9), this device's setting. */
  sideChats: boolean
  /**
   * 0.29: the sessions whose cloud the person turned on here (C11). A chat that came from the cloud
   * is not in this list but in `sessions`; the main chat is in the cloud whether it is listed or not.
   */
  synced: string[]
  available: boolean
  paused: boolean
  cursor: number
  relay: SyncRelayState | null
  /** Grows with every change applied here; the browser half re-reads a session's remote rows when it moves. */
  rev: number
  /** The session that holds the account's main conversation ('' before the chats column named one). */
  mainSession: string
  /** Sessions that hold a synced conversation: listed in the chats column even before a turn ran here. */
  sessions: string[]
  /** The title of each synced session, so a chat the harness has not listed yet still has a name. */
  titles: Record<string, string>
  /** Sessions that belong to another account than the one signed in (C10): hidden by the browser half, not deleted; empty when signed out. */
  foreign: string[]
  /** mids of rows deleted elsewhere: hidden in the transcript. */
  hidden: string[]
  /** Sessions whose conversation was deleted elsewhere: to be archived by the browser half. */
  toRestore?: string[]
  toArchive: string[]
}

/** Another device working on a conversation that lives in a session here (C9), for the browser half's line. */
export interface WorkingPresence {
  from: string
  deviceName: string
  /** Unix epoch milliseconds. */
  at: number
}

export class SyncEngine {
  state: SyncState
  private paused = false
  private pushTimer: ReturnType<typeof setTimeout> | undefined
  private pullTimer: ReturnType<typeof setInterval> | undefined
  private frameTimer: ReturnType<typeof setTimeout> | undefined
  private workingTimer: ReturnType<typeof setTimeout> | undefined
  private readonly dirty = new Set<string>()
  /** Sessions whose turn started (true) or ended (false) here, told to the relay after the push (C9). */
  private readonly presence = new Map<string, boolean>()
  /** cid → device → the other device working on it (C9); two devices may overlap. */
  private readonly working = new Map<string, Map<string, WorkingPresence>>()
  private pulling: Promise<number> | undefined
  private pushing: Promise<void> | undefined
  private frameCursor = 0
  private frameNeedsPull = false
  private relayState: SyncRelayState | null = null
  private lastError = ''
  private rev = 0
  /** Signed in, as the host last said (`start` / `accountChanged` vs `signedOut`); decides whether another account's sessions are hidden (C10). */
  private signedIn = false
  private catalogSupported: boolean | undefined
  private readonly selectedSessions = new Set<string>()
  private readonly viewSelections = new Map<string, { ids: string[]; until: number }>()
  private readonly messageCursors = new Map<string, number>()
  private readonly preparingSessions = new Set<string>()
  private readonly unread = new Map<string, number>()
  private catalogueSignature = ''
  private catalogueIds = new Set<string>()

  private refreshSelections(): string[] {
    const now = Date.now()
    for (const [reader, view] of this.viewSelections) if (view.until < now) this.viewSelections.delete(reader)
    return [...new Set([...this.viewSelections.values()].flatMap(v => v.ids))]
  }

  async selectSessions(ids: string[], reader = 'desktop-view'): Promise<void> {
    if (!/^[a-zA-Z0-9._:-]{4,80}$/.test(reader)) throw new Error('invalid chat reader')
    const selected = [...new Set(ids.filter((id) => this.owns(id)))].slice(0, 8)
    if (selected.length) this.viewSelections.set(reader, { ids: selected, until: Date.now() + 45_000 })
    else this.viewSelections.delete(reader)
    if (this.viewSelections.size > 128) this.viewSelections.delete(this.viewSelections.keys().next().value!)
    const next = this.refreshSelections()
    if (next.length === this.selectedSessions.size && next.every((id) => this.selectedSessions.has(id))) return
    this.selectedSessions.clear()
    next.forEach((id) => this.selectedSessions.add(id))
    await this.pullQuietly()
  }

  async resolveCid(cid: string, loadHistory = false): Promise<string | undefined> {
    const account = this.state.accountId
    if (!(await this.active())) return undefined
    // A task cannot use a cached catalogue after a failed refresh.
    await this.pull()
    if (account !== this.state.accountId) return undefined
    if (!this.catalogueIds.has(cid)) return undefined
    const sessionId = this.sessionOf(cid)
    if (!sessionId || this.syncPaused(sessionId) || this.state.conversations[cid]?.archived) return undefined
    if (sessionId && loadHistory) {
      this.preparingSessions.add(sessionId)
      try {
        await this.pull()
        if (account !== this.state.accountId || this.syncPaused(sessionId) || this.state.conversations[cid]?.archived || this.sessionOf(cid) !== sessionId) return undefined
        // Rebuild model-facing context from the persisted shared rows, including turns
        // received before a restart or a previously failed background injection.
        const lines = this.options.sessions.remote?.(sessionId) ?? []
        if (lines.length) await this.options.sessions.inject(sessionId, meanwhileNote(lines))
      } finally { this.preparingSessions.delete(sessionId) }
    }
    return sessionId
  }


  constructor(private readonly options: SyncEngineOptions) {
    const loaded = options.load()
    this.state = migrate(loaded)
    // a state from before the per-chat cloud (C11): what is in the account already keeps its cloud
    if (loaded && !('clouds' in loaded)) this.reconcileClouds()
    for (const [cid, seq] of Object.entries(this.state.messageCursors ?? {})) if (Number.isSafeInteger(seq) && seq >= 0) this.messageCursors.set(cid, seq)
  }

  get enabled(): boolean {
    return this.state.enabled
  }

  /** On, signed in, and the key not refused. */
  async active(): Promise<boolean> {
    return this.state.enabled && !this.paused && Boolean(await this.options.token())
  }

  view(): SyncView {
    return {
      enabled: this.state.enabled,
      catalogue: this.catalogSupported === true,
      unread: Object.fromEntries([...this.unread].map(([cid, count]) => [this.sessionOf(cid) ?? cid, count])),
      sideChats: this.state.sideChats,
      synced: Object.entries(this.state.clouds).filter(([, mark]) => mark === 'device').map(([sid]) => sid),
      available: !this.paused && Boolean(this.state.accountId),
      paused: this.paused,
      cursor: this.state.cursor,
      relay: this.relayState,
      rev: this.rev,
      mainSession: this.state.mainSession,
      sessions: Object.keys(this.state.cids).filter((sid) => this.owns(sid) && this.state.clouds[sid] !== 'local'),
      titles: Object.fromEntries(
        Object.entries(this.state.titles).filter(([sid]) => this.owns(sid) && this.state.clouds[sid] !== 'local'),
      ),
      foreign: this.foreign(),
      hidden: [...this.state.hidden],
      toArchive: this.state.toArchive.filter(sid => this.state.clouds[sid] !== 'local' || !this.state.cids[sid]),
      toRestore: Object.entries(this.state.cids).filter(([sid, cid]) => this.owns(sid) && this.state.clouds[sid] !== 'local' && this.state.conversations[cid]?.archived === false && !(sid in (this.state.pendingArchives ?? {}))).map(([sid]) => sid),
    }
  }

  /** The conversation id a session syncs under, when it has one. */
  cidOf(sessionId: string): string | undefined {
    return this.state.cids[sessionId]
  }

  /**
   * Whether a session is the signed-in account's or nobody's yet (C10): what is shown, pushed
   * and dressed with the other devices' turns. Signed out, every session is.
   */
  owns(sessionId: string): boolean {
    if (!this.signedIn || !this.state.accountId) return true
    const owner = this.state.owners[sessionId]
    return !owner || owner === this.state.accountId
  }

  /** The sessions another account owns, while signed in (C10): hidden, not deleted. */
  private foreign(): string[] {
    if (!this.signedIn || !this.state.accountId) return []
    return Object.entries(this.state.owners).filter(([, owner]) => owner !== this.state.accountId).map(([sid]) => sid)
  }

  /** The session joins the signed-in account (C10): on its first push, or when a pull gives it a conversation. */
  private adopt(sessionId: string): void {
    if (this.state.accountId && !this.state.owners[sessionId]) this.state.owners[sessionId] = this.state.accountId
  }

  // ---- lifecycle ----------------------------------------------------------------------------

  start(): void {
    this.signedIn = true
    this.pullTimer ??= setInterval(() => void this.pullQuietly(), this.options.pullEveryMs ?? PULL_EVERY_MS)
    // Keep chats that already existed on this computer local by default. The first message
    // in a later-created chat will put its cloud on unless the person explicitly turned it off.
    void this.options.sessions.list().then(async (sessions) => {
      if (!this.state.sideChats) {
        for (const session of sessions) {
          if (!session.blank && session.id !== this.state.mainSession && !this.state.cids[session.id] && this.state.clouds[session.id] === undefined) this.state.clouds[session.id] = 'local'
        }
        await this.save()
      }
      await this.pullQuietly()
      this.pushAllSoon()
    }).catch(() => undefined)
  }

  stop(): void {
    if (this.pullTimer) clearInterval(this.pullTimer)
    this.pullTimer = undefined
    if (this.pushTimer) clearTimeout(this.pushTimer)
    this.pushTimer = undefined
    if (this.frameTimer) clearTimeout(this.frameTimer)
    this.frameTimer = undefined
    if (this.workingTimer) clearTimeout(this.workingTimer)
    this.workingTimer = undefined
  }

  /**
   * A sign-in (C10). A different account than the last one starts from cursor 0 — the
   * relay's rows, the kept turns and the presence of the last account are dropped, every
   * account's mapping is kept — and whatever this does to the view is told. The same
   * account again (or a first sign-in) changes nothing but the pull that follows.
   */
  async accountChanged(accountId: string): Promise<void> {
    this.paused = false
    this.signedIn = true
    // a different account than the one this device last synced with (the first sign-in ever is not a switch)
    const changed = Boolean(accountId) && Boolean(this.state.accountId) && accountId !== this.state.accountId
    if (changed) {
      this.selectedSessions.clear()
      this.viewSelections.clear()
      this.preparingSessions.clear()
      this.messageCursors.clear()
      this.unread.clear()
      this.catalogueIds.clear()
      this.catalogueSignature = ''
      this.state = forAccount(this.state, accountId)
      this.state.messageCursors = {}
      this.working.clear()
      this.presence.clear()
      this.relayState = null
      await this.options.sessions.forgetAll?.().catch(() => undefined)
      await this.save()
      this.log('info', 'nanomuse sync: another account signed in; its conversations are pulled afresh, the last account’s stay hidden')
    } else if (accountId && !this.state.accountId) {
      this.state.accountId = accountId
      await this.save()
    }
    if (changed) this.changed()
    // the pull from zero follows, on its own tick, so the reset state is what a caller sees first
    const timer = setTimeout(() => void this.pullQuietly().then(() => this.pushAllSoon()), 0)
    timer.unref?.()
  }

  /** A sign-out: nothing is dropped (the account is remembered, so a different one next is known); every local chat shows. */
  signedOut(): void {
    this.paused = false
    this.signedIn = false
    this.viewSelections.clear()
    this.preparingSessions.clear()
    this.selectedSessions.clear()
    this.unread.clear()
    this.relayState = null
    this.working.clear()
    this.presence.clear()
    this.changed()
  }

  /**
   * The chats column's main chat. While the account's main conversation already lives in a
   * session here, that session stays the main chat (one thread, C8): the id of the one it
   * lives in comes back for the column to adopt. Otherwise the session becomes the main chat
   * and takes the relay's main conversation id when the relay has one. A session of another
   * account cannot become the main chat (C10).
   */
  async setMain(sessionId: string): Promise<string> {
    const current = this.state.mainSession
    if (!sessionId || current === sessionId) return current
    if (!this.owns(sessionId)) return current
    if (current && this.holdsMain(current)) return current
    this.state.mainSession = sessionId
    // the chat that was the main one keeps its conversation, so it stays in the cloud as a side
    // chat from here on, never silently dropped out of it (C11)
    if (current && current !== sessionId) this.noteCloud(current, '')
    const mainCid = this.mainCid()
    if (mainCid && !this.state.cids[sessionId]) {
      this.state.cids[sessionId] = mainCid
      this.adopt(sessionId)
      this.state.titles[sessionId] = this.state.conversations[mainCid]?.title ?? ''
    } else {
      delete this.state.titles[sessionId]
    }
    await this.save()
    this.dirty.add(sessionId)
    this.pushSoon(200)
    this.changed()
    return sessionId
  }

  /** Archive a shared chat without deleting its transcript; local chat state remains on the host. */
  async setArchived(sessionId: string, archived: boolean): Promise<void> {
    const cid = this.state.cids[sessionId]
    if (!this.state.accountId || !this.syncs(sessionId)) return
    this.state.pendingArchives ??= {}
    this.state.pendingArchives[sessionId] = archived
    const known = cid ? this.state.conversations[cid] : undefined
    if (known) known.archived = archived
    this.state.toArchive = this.state.toArchive.filter(id => id !== sessionId)
    await this.save()
    this.changed()
    try { await this.flushArchives() } catch (error) { this.noteError(error) }
  }

  private archiveFlushing: Promise<void> | undefined

  private flushArchives(): Promise<void> {
    if (this.archiveFlushing) return this.archiveFlushing
    this.archiveFlushing = this.flushArchiveBatch().finally(() => { this.archiveFlushing = undefined })
    return this.archiveFlushing
  }

  private async flushArchiveBatch(): Promise<void> {
    if (!this.state.enabled || !this.signedIn) return
    const token = await this.options.token()
    if (!token) return
    const account = this.state.accountId
    for (const [sid, archived] of Object.entries(this.state.pendingArchives ?? {})) {
      if (this.state.accountId !== account) return
      const cid = this.state.cids[sid]
      if (!cid || !this.owns(sid) || this.state.clouds[sid] === 'local') continue
      try {
        await this.options.relay.setArchived(token, cid, archived)
      } catch (error) {
        if (error instanceof RelayError && error.status === 404 && this.state.accountId === account && this.state.cids[sid] === cid) {
          delete this.state.pendingArchives?.[sid]
          await this.save()
          continue
        }
        throw error
      }
      if (this.state.accountId === account && this.state.pendingArchives?.[sid] === archived) {
        delete this.state.pendingArchives[sid]
        await this.save()
      }
    }
  }

  /** The browser half archived the sessions whose conversations were deleted elsewhere. */
  async archived(sessionIds: string[]): Promise<void> {
    const done = new Set(sessionIds)
    this.state.toArchive = this.state.toArchive.filter((id) => !done.has(id))
    await this.save()
    this.changed()
  }

  private holdsMain(sessionId: string): boolean {
    const cid = this.state.cids[sessionId]
    return Boolean(cid && this.state.conversations[cid]?.kind === 'main')
  }

  private mainCid(): string | undefined {
    for (const [cid, c] of Object.entries(this.state.conversations)) if (c.kind === 'main') return cid
    return undefined
  }

  // ---- the switch ---------------------------------------------------------------------------

  async setEnabled(enabled: boolean): Promise<SyncView> {
    const token = await this.options.token()
    if (token) {
      try {
        this.relayState = await this.options.relay.setEnabled(token, enabled)
      } catch (error: unknown) {
        this.noteError(error)
        throw error
      }
    }
    this.state.enabled = enabled
    if (enabled) {
      this.paused = false
      // on again: this device's conversations go up in full — the signed-in account's and the
      // ownerless; another account's lines keep their ids (its relay store was not emptied, C10)
      const keep = new Set<string>()
      for (const sid of this.foreign()) for (const line of await this.options.sessions.lines(sid).catch(() => [] as SessionLine[])) keep.add(line.id)
      this.state.titles = Object.fromEntries(Object.entries(this.state.titles).filter(([sid]) => !this.owns(sid)))
      this.state.mids = Object.fromEntries(Object.entries(this.state.mids).filter(([id]) => keep.has(id)))
    }
    await this.save()
    if (enabled) void this.pullQuietly().then(() => this.pushAllSoon())
    this.changed()
    return this.view()
  }

  /**
   * "Also sync side chats" (C9), this device's setting. On: the side chats here go up (oldest
   * first, as the history did) after one pull of the account's newest side turns
   * (`since=0&scope=all&tail=300`, idempotent by mid and cid). Off: side chats stop going
   * up and coming down; what was synced stays where it is.
   */
  async setSideChats(on: boolean): Promise<SyncView> {
    if (on === this.state.sideChats) return this.view()
    this.state.sideChats = on
    await this.save()
    if (on) void this.pullTail().then(() => this.pushAllSoon())
    this.changed()
    return this.view()
  }

  /** Pause this computer's exchange without deleting the shared conversation or its mapping. */
  async setSynced(sessionId: string, on: boolean): Promise<SyncedOutcome> {
    const refusal = this.cloudRefusal(sessionId)
    if (refusal) return { ...this.view(), ok: false, reason: refusal }
    this.state.clouds[sessionId] = on ? 'device' : 'local'
    if (!on) {
      this.dirty.delete(sessionId)
      delete this.state.pendingArchives?.[sessionId]
      this.state.toArchive = this.state.toArchive.filter(id => id !== sessionId)
    }
    await this.save()
    if (on) void this.pullTail().then(() => this.pushAllSoon())
    this.changed()
    return { ...this.view(), ok: true }
  }

  /**
   * Take a chat out of the account for good (C11): a conversation that came by sync cannot be made
   * local, only deleted from it. The relay's copy is tombstoned, so the chat is gone from the other
   * devices and from the messengers, and the copy here stays as a local chat. The main chat cannot
   * go (the account keeps its one main conversation), and a chat that is local already has nothing
   * to delete and answers `ok`.
   */
  async deleteConversation(sessionId: string): Promise<SyncedOutcome> {
    if (!sessionId) return { ...this.view(), ok: false, reason: 'unknown-session' }
    if (sessionId === this.state.mainSession || this.holdsMain(sessionId)) return { ...this.view(), ok: false, reason: 'main' }
    if (!this.state.enabled || !this.signedIn || !this.state.accountId || !this.owns(sessionId)) return { ...this.view(), ok: false, reason: 'not-synced-account' }
    await this.cloudOff(sessionId)
    await this.save()
    this.changed()
    return { ...this.view(), ok: true }
  }

  /** The account's main chat stays shared; other chats can pause locally. */
  private cloudRefusal(sessionId: string): CloudRefusal | undefined {
    if (!sessionId) return 'unknown-session'
    if (sessionId === this.state.mainSession || this.holdsMain(sessionId)) return 'main'
    if (!this.state.enabled || !this.signedIn || !this.state.accountId || !this.owns(sessionId)) return 'not-synced-account'

    return undefined
  }

  /** Explicit account deletion is separate from pausing this computer's sync. */
  private async cloudOff(sessionId: string): Promise<void> {
    const cid = this.state.cids[sessionId]
    if (cid && sessionId !== this.state.mainSession) {
      try {
        const token = await this.options.token()
        if (token) await this.options.relay.deleteConversation(token, cid, this.options.deviceId())
      } catch {
        // the account could not be reached: the chat is local here all the same
      }
      delete this.state.cids[sessionId]
      delete this.state.owners[sessionId]
      delete this.state.titles[sessionId]
      delete this.state.conversations[cid]
      for (const mid of Object.keys(this.state.pulled)) {
        if (this.state.pulled[mid] === sessionId) delete this.state.pulled[mid]
      }
    }
    this.state.clouds[sessionId] = 'local'
    this.dirty.delete(sessionId)
  }

  async relayStatus(): Promise<SyncRelayState | null> {
    const token = await this.options.token()
    if (!token) return (this.relayState = null)
    try {
      this.relayState = await this.options.relay.state(token)
      for (const w of this.relayState.working) this.noteWorking(w, false)
      this.expireWorking()
    } catch (error: unknown) {
      this.noteError(error)
    }
    return this.relayState
  }

  // ---- presence (C9) ------------------------------------------------------------------------

  /** The hub's `working` frame: another device started or finished a turn on a conversation. */
  onWorking(frame: WireWorking): void {
    if (this.noteWorking(frame, true)) this.changed()
  }

  /** The other device working on the conversation that lives in a session here, if any (and not 10 minutes old); never for another account's session (C10). */
  workingOf(sessionId: string): WorkingPresence | null {
    this.expireWorking()
    if (!this.owns(sessionId)) return null
    const cid = this.state.cids[sessionId]
    const by = cid ? this.working.get(cid) : undefined
    if (!by?.size) return null
    return [...by.values()].sort((a, b) => b.at - a.at)[0] ?? null
  }

  /** Whether the line the browser half shows moved. */
  private noteWorking(w: WireWorking, tell: boolean): boolean {
    if (!w.cid || w.from === this.options.deviceId()) return false
    const at = w.at * 1000
    let by = this.working.get(w.cid)
    if (!w.working || Date.now() - at >= WORKING_TTL_MS) {
      if (!by) return false
      const dropped = by.delete(w.from) || by.size === 0
      if (!by.size) this.working.delete(w.cid)
      return dropped
    }
    const known = by?.get(w.from)
    if (known && known.at >= at) return false
    if (!by) {
      by = new Map()
      this.working.set(w.cid, by)
    }
    by.set(w.from, { from: w.from, deviceName: w.device_name, at })
    // the line goes away by itself ten minutes after the newest `at` in this chat
    if (this.workingTimer) clearTimeout(this.workingTimer)
    const until = Math.max(...[...by.values()].map((p) => p.at)) + WORKING_TTL_MS
    this.workingTimer = setTimeout(() => {
      this.workingTimer = undefined
      if (this.expireWorking()) this.changed()
    }, Math.max(0, until - Date.now()) + 50)
    return tell
  }

  private expireWorking(): boolean {
    let dropped = false
    for (const [cid, by] of this.working) {
      for (const [device, w] of by) {
        if (Date.now() - w.at >= WORKING_TTL_MS) {
          by.delete(device)
          dropped = true
        }
      }
      if (!by.size) this.working.delete(cid)
    }
    return dropped
  }

  /** One device's reply landed: its presence goes, the other device's stays. */
  private clearWorking(cid: string, device: string): void {
    const by = this.working.get(cid)
    if (!by) return
    by.delete(device)
    if (!by.size) this.working.delete(cid)
  }

  /** Told to the relay after the push: best-effort, once, never blocking (C9). */
  private tellWorking(token: string, sessionId: string): void {
    const working = this.presence.get(sessionId)
    this.presence.delete(sessionId)
    if (working === undefined || !this.syncs(sessionId)) return
    const cid = this.state.cids[sessionId]
    if (cid) void this.options.relay.working(token, this.options.deviceId(), cid, working).catch(() => undefined)
  }

  /** "Delete synced conversations": the relay's store emptied; what it knew is forgotten here, the sessions stay, and every chat that was in the cloud reads as local (C11). */
  async deleteRemote(): Promise<SyncView> {
    const token = await this.options.token()
    if (token) {
      this.relayState = await this.options.relay.wipe(token)
      this.state.cursor = this.relayState.cursor
    }
    this.state.conversations = {}
    this.state.mids = {}
    this.state.titles = {}
    this.state.clouds = {}
    await this.save()
    this.changed()
    return this.view()
  }

  // ---- hooks --------------------------------------------------------------------------------

  /** The person sent a message in a session here: it goes up now (C8), not when the turn ends. */
  messageSent(sessionId: string): void {
    if (!this.syncs(sessionId)) return
    this.dirty.add(sessionId)
    this.presence.set(sessionId, true)
    this.pushSoon(Math.min(SEND_DELAY_MS, this.options.pushDelayMs ?? PUSH_DELAY_MS))
    // a new prompt here is a new anchor for the other devices' bubbles: the browser half re-reads them
    if (this.kept(sessionId)) this.changed()
  }

  /** Whether a session shows turns from the other devices (a pulled row, or one hidden). */
  private kept(sessionId: string): boolean {
    return Object.values(this.state.pulled).includes(sessionId)
  }

  /**
   * Whether a session's turns go up (C11): the main chat always; another device's task too (its
   * answers already travel as the task's result, so only the person's own words go up from it); a
   * chat whose cloud the person turned on here, or one that came from the cloud; and with the older
   * device-wide "Also sync side chats" switch on, every chat of this device except the one whose
   * cloud the person took off. Another account's chat never (C10).
   */
  private syncs(sessionId: string): boolean {
    if (!this.owns(sessionId)) return false
    if (this.options.isTaskSession?.(sessionId) === true) return true
    if (sessionId === this.state.mainSession) return true
    const mark = this.state.clouds[sessionId]
    if (mark === 'device' || mark === 'sync') return true
    if (mark === 'local') return false
    return this.state.sideChats
  }

  /** A turn ended in a session here: the model's final text goes up after the debounce. A task session's answer does not: the device that asked already has it as the call's result. */
  turnEnded(sessionId: string): void {
    if (this.options.isTaskSession?.(sessionId)) return
    if (!this.syncs(sessionId)) return
    this.dirty.add(sessionId)
    this.presence.set(sessionId, false)
    this.pushSoon(this.options.pushDelayMs ?? PUSH_DELAY_MS)
  }

  sessionRenamed(sessionId: string): void {
    if (!this.syncs(sessionId)) return
    this.dirty.add(sessionId)
    this.pushSoon(200)
  }
  /** A local answer presented a file: put its link into the shared transcript as an assistant
   * line, so the other devices (and the Telegram bot) can deliver it too. The local session
   * already draws the preview; the synthetic mid is remembered so the relay echo is not shown
   * here twice. */
  async sharePresented(sessionId: string, text: string): Promise<void> {
    if (!text.trim() || !this.syncs(sessionId) || this.options.isTaskSession?.(sessionId) === true) return
    if (!(await this.active())) return
    const token = await this.options.token()
    if (!token) return
    const cid = this.cidFor(sessionId)
    const mid = randomUUID()
    try {
      const out = await this.options.relay.push(token, this.options.deviceId(), [], [
        { mid, cid, role: 'assistant', text, created_at: Math.floor(Date.now() / 1000) },
      ])
      if (out.accepted > 0) {
        this.state.mids[`presented:${mid}`] = mid
        await this.save()
      }
    } catch {
      // the local preview stays; a later turn retries the normal sync path
    }
  }


  /** The relay's `sync` frame: another device pushed. Frames arriving together become one pull, and a frame arriving during an in-flight pull runs a follow-up pull when needed. */
  onFrame(frame: { cursor?: number; from?: string }): void {
    if (frame.from && frame.from === this.options.deviceId()) return
    if (typeof frame.cursor === 'number') {
      if (frame.cursor <= this.state.cursor) return
      if (frame.cursor > this.frameCursor) this.frameCursor = frame.cursor
    } else {
      this.frameNeedsPull = true
    }
    if (this.frameTimer) return
    this.frameTimer = setTimeout(() => {
      this.frameTimer = undefined
      this.drainFramePull()
    }, this.options.frameCoalesceMs ?? FRAME_COALESCE_MS)
  }

  private drainFramePull(): void {
    if (this.pulling) {
      void this.pulling.finally(() => {
        if (this.frameNeedsPull || this.frameCursor > this.state.cursor) {
          this.frameNeedsPull = false
          void this.pull().catch(() => 0)
        }
      })
      return
    }
    this.frameNeedsPull = false
    void this.pullQuietly()
  }

  // ---- push ---------------------------------------------------------------------------------

  private pushSoon(delayMs: number): void {
    if (this.pushTimer) clearTimeout(this.pushTimer)
    this.pushTimer = setTimeout(() => {
      this.pushTimer = undefined
      void this.push().catch(() => undefined)
    }, delayMs)
  }

  /** Every session that is not a task: on start and when the switch goes on. */
  private pushAllSoon(): void {
    void this.options.sessions
      .list()
      .then((sessions) => {
        for (const s of sessions) if (!s.blank && this.syncs(s.id)) this.dirty.add(s.id)
        this.pushSoon(this.options.pushDelayMs ?? PUSH_DELAY_MS)
      })
      .catch(() => undefined)
  }

  /** The dirty sessions' new lines to the relay, oldest session first, one POST of up to 200 messages at a time; one push at a time. */
  push(): Promise<void> {
    const run = (): Promise<void> => {
      const p = this.pushNow()
        .catch((error: unknown) => {
          this.noteError(error)
          throw error
        })
        .finally(() => {
          if (this.pushing === p) this.pushing = undefined
        })
      this.pushing = p
      return p
    }
    return this.pushing ? this.pushing.then(run, run) : run()
  }

  private async pushNow(): Promise<void> {
    if (!(await this.active())) return
    const token = await this.options.token()
    if (!token) return
    const device = this.options.deviceId()
    const sessions = new Map((await this.options.sessions.list()).map((s) => [s.id, s]))
    // oldest first: a history going up in full arrives in the order it was lived
    const ids = [...this.dirty].sort((a, b) => (sessions.get(a)?.createdAt ?? 0) - (sessions.get(b)?.createdAt ?? 0))
    this.dirty.clear()
    for (let round = 0; round < 50 && ids.length > 0; round++) {
      const conversations: Record<string, unknown>[] = []
      const messages: Record<string, unknown>[] = []
      const pending: Array<{ sessionId: string; line: SessionLine; mid: string }> = []
      const touched: string[] = []
      // sessions with more than 200 new lines and those the round did not reach go again
      const again = new Set(ids)
      for (const sessionId of ids) {
        again.delete(sessionId)
        const info = sessions.get(sessionId)
        if (!info || !this.syncs(sessionId)) continue
        const cid = this.cidFor(sessionId)
        const title = info.title || 'New chat'
        if (this.state.titles[sessionId] !== title) {
          conversations.push({ cid, kind: sessionId === this.state.mainSession ? 'main' : 'side', title, created_at: Math.floor(info.createdAt / 1000), updated_at: Math.floor(info.updatedAt / 1000) })
        }
        // a task session's answer is the call's result over there: only the person's own
        // words typed here go up, never the model's
        const task = this.options.isTaskSession?.(sessionId) === true
        const lines = (await this.options.sessions.lines(sessionId)).filter((line) => !task || line.role === 'user')
        for (const line of lines) {
          if (this.state.mids[line.id]) continue
          if (messages.length >= MAX_POST_MESSAGES) {
            again.add(sessionId)
            break
          }
          const mid = randomUUID()
          messages.push({ mid, cid, role: line.role, text: line.text, created_at: Math.floor(line.at / 1000) })
          pending.push({ sessionId, line, mid })
        }
        touched.push(sessionId)
        if (messages.length >= MAX_POST_MESSAGES) break
      }
      if (conversations.length === 0 && messages.length === 0) {
        // nothing new in these sessions: they are done, not dirty again
        for (const sid of touched) this.tellWorking(token, sid)
        ids.splice(0, ids.length, ...ids.filter((sid) => again.has(sid)))
        break
      }
      const out = await this.options.relay.push(token, device, conversations, messages)
      // the relay took the round: an ownerless session is the signed-in account's from now on (C10)
      for (const sid of touched) this.adopt(sid)
      const redirect = new Map<string, string>()
      for (const r of out.rejected) if (r.reason === 'main_exists' && r.cid && r.cid_main) redirect.set(r.cid, r.cid_main)
      if (redirect.size > 0) {
        // the account's main chat has an id already: ours takes it; its lines go again next round
        for (const [sid, cid] of Object.entries(this.state.cids)) {
          const to = redirect.get(cid)
          if (to) {
            this.state.cids[sid] = to
            delete this.state.titles[sid]
            again.add(sid)
            this.log('info', 'nanomuse sync: the main chat adopts the account’s conversation id')
          }
        }
      }
      const refused = new Map(out.rejected.filter((r) => r.mid && r.reason !== 'main_exists').map((r) => [r.mid!, r.reason]))
      const redirected = new Set(out.rejected.filter((r) => r.mid && r.reason === 'main_exists').map((r) => r.mid!))
      for (const p of pending) {
        if (redirected.has(p.mid)) continue
        // a refused row stays refused: it is not sent again and again
        this.state.mids[p.line.id] = refused.has(p.mid) ? `refused:${refused.get(p.mid)}` : p.mid
      }
      for (const c of conversations) {
        if (out.rejected.some((r) => r.cid === c.cid)) continue
        for (const [sid, cid] of Object.entries(this.state.cids)) if (cid === c.cid) this.state.titles[sid] = String(c.title)
        const known = this.state.conversations[String(c.cid)]
        this.state.conversations[String(c.cid)] = {
          kind: c.kind === 'main' ? 'main' : 'side',
          title: String(c.title),
          device: known?.device || device,
          deviceName: known?.deviceName ?? '',
          createdAt: known?.createdAt || Number(c.created_at),
          updatedAt: Math.max(known?.updatedAt ?? 0, Number(c.updated_at)),
        }
      }
      await this.save()
      // the turn's start or end told once the words are up (C9)
      for (const sid of touched) if (!again.has(sid)) this.tellWorking(token, sid)
      ids.splice(0, ids.length, ...ids.filter((sid) => again.has(sid)))
    }
    if (ids.length > 0) {
      // a very long history: the rest goes after the next debounce
      for (const sid of ids) this.dirty.add(sid)
      this.pushSoon(this.options.pushDelayMs ?? PUSH_DELAY_MS)
    }
    // our rows come back on the next pull (known by device, no change) and the cursor catches up
    void this.pullQuietly()
  }

  private cidFor(sessionId: string): string {
    let cid = this.state.cids[sessionId]
    if (!cid) {
      // the main chat takes the account's main conversation id when the relay has one
      cid = (sessionId === this.state.mainSession ? this.mainCid() : undefined) ?? randomUUID()
      this.state.cids[sessionId] = cid
    }
    return cid
  }

  // ---- pull ---------------------------------------------------------------------------------

  /** A pull that joins the one in flight, and tells nobody when it fails (rule 7). */
  private pullQuietly(): Promise<number> {
    return (this.pulling ?? this.pull()).catch(() => 0)
  }

  /** What the other devices pushed since our cursor, applied in order; how many rows. One at a time. */
  pull(tail = false): Promise<number> {
    const run = (): Promise<number> => {
      const p = this.pullNow(tail)
        .catch((error: unknown) => {
          this.noteError(error)
          throw error
        })
        .finally(() => {
          if (this.pulling === p) this.pulling = undefined
        })
      this.pulling = p
      return p
    }
    return this.pulling ? this.pulling.then(run, run) : run()
  }

  /** The side-chat switch went on: the account's newest side turns, once, from the start (C9). */
  private pullTail(): Promise<number> {
    return this.pull(true).catch(() => 0)
  }

  private syncPaused(sessionId: string): boolean { return this.state.clouds[sessionId] === 'local' }

  private async pullCatalogue(token: string): Promise<number | undefined> {
    if (this.catalogSupported === false) return undefined
    const account = this.state.accountId
    let data: Awaited<ReturnType<SyncRelay['catalog']>>
    try {
      data = await this.options.relay.catalog(token, this.options.deviceId(), this.options.deviceId())
    } catch (error) {
      if (error instanceof RelayError && error.status === 404) { this.catalogSupported = false; return undefined }
      throw error
    }
    if (account !== this.state.accountId || (data.account_id && data.account_id !== account)) return 0
    this.catalogSupported = true
    const ids = new Set(data.conversations.map((c) => c.cid))
    for (const row of data.deleted ?? []) {
      await this.applyConversation(row)
      this.unread.delete(row.cid)
    }
    this.catalogueIds = ids
    let changed = this.catalogueSignature !== JSON.stringify(data)
    this.catalogueSignature = JSON.stringify(data)
    for (const c of data.conversations) { await this.applyConversation(c); this.unread.set(c.cid, c.unread || 0) }
    // Messenger conversations that arrived before this build have no cursor and used to
    // stay blank on the computer. Pull their first page once, so the chat is a chat here
    // and not an empty row only the web can see.
    for (const c of data.conversations) {
      if (c.archived || !/^(MAX|Telegram) ·/.test(String(c.title || ''))) continue
      const sid = this.sessionOf(c.cid)
      if (!sid || !this.owns(sid) || this.state.clouds[sid] === 'local' || this.messageCursors.has(c.cid)) continue
      this.viewSelections.set(`sync-new:${sid}`, { ids: [sid], until: Date.now() + 45_000 })
    }
    const main = data.conversations.find(c => c.kind === 'main' && !c.deleted)
    const mainSession = main ? this.sessionOf(main.cid) : undefined
    if (mainSession && mainSession !== this.state.mainSession) {
      this.state.mainSession = mainSession
      for (const [sid, cid] of Object.entries(this.state.cids)) {
        if (sid !== mainSession && this.state.conversations[cid]?.archived && this.state.clouds[sid] !== 'local' && !this.state.toArchive.includes(sid)) this.state.toArchive.push(sid)
      }
      changed = true
    }
    let applied = 0
    const notes = new Map<string, RemoteLine[]>()
    const visible = this.refreshSelections()
    this.selectedSessions.clear()
    visible.forEach(id => this.selectedSessions.add(id))
    for (const sessionId of new Set([...this.selectedSessions, ...this.preparingSessions])) {
      const cid = this.cidOf(sessionId)
      if (!this.owns(sessionId) || !cid || !ids.has(cid) || this.state.clouds[sessionId] === 'local') continue
      for (let round = 0; round < 50; round++) {
        const page = await this.options.relay.chatMessages(token, cid, this.messageCursors.get(cid) ?? 0)
        if (account !== this.state.accountId || (page.account_id && page.account_id !== account) || this.syncPaused(sessionId) || (!this.selectedSessions.has(sessionId) && !this.preparingSessions.has(sessionId))) break
        for (const m of page.messages) { await this.applyMessage(m, notes); applied++ }
        this.messageCursors.set(cid, page.cursor)
        if (!page.more) {
          if (this.selectedSessions.has(sessionId)) {
            await this.options.relay.markRead(token, cid, this.options.deviceId(), page.through_seq)
            if (this.unread.get(cid)) changed = true
            this.unread.set(cid, 0)
          }
          break
        }
      }
    }
    if (account !== this.state.accountId) return 0
    this.state.cursor = data.cursor
    this.state.messageCursors = Object.fromEntries(this.messageCursors)
    await this.save()
    for (const [sid, lines] of notes) await this.options.sessions.inject(sid, meanwhileNote(lines)).catch(() => undefined)
    if (changed || applied) this.changed()
    return applied
  }

  private async pullNow(tail: boolean): Promise<number> {
    if (!(await this.active())) return 0
    const token = await this.options.token()
    if (!token) return 0
    await this.flushArchives()
    const lazy = await this.pullCatalogue(token)
    if (lazy !== undefined) return lazy
    let applied = 0
    // what the other devices added to conversations living in sessions here: context for the model, per session
    const notes = new Map<string, RemoteLine[]>()
    const scope = this.state.sideChats || Object.entries(this.state.clouds).some(([sid, mark]) => mark !== 'local' && sid !== this.state.mainSession) ? 'all' : 'main'
    const cursors = this.state.cursors ?? { main: 0, all: this.state.cursor }
    for (let page = 0; page < 50; page++) {
      // the first pull after sign-in asks for the newest 300 of the scope (C9): the chat shows before the history
      const since = tail ? 0 : (cursors[scope] ?? this.state.cursor)
      const out = await this.options.relay.changes(token, since, PAGE, scope, since === 0 ? TAIL : 0)
      // The page's conversations first, then its messages, each in seq order: a rename puts
      // a conversation's seq above its messages, and the relay sends every message's
      // conversation along with the page so none of them is an orphan.
      for (const c of [...out.conversations].sort((a, b) => a.seq - b.seq)) {
        await this.applyConversation(c)
        applied += 1
      }
      for (const m of [...out.messages].sort((a, b) => a.seq - b.seq)) {
        await this.applyMessage(m, notes)
        applied += 1
      }
      cursors[scope] = Math.max(cursors[scope] ?? 0, out.cursor)
      this.state.cursors = cursors
      this.state.cursor = Math.max(cursors.main ?? 0, cursors.all ?? 0)
      await this.save()
      if (!out.more || tail) break
    }
    this.lastError = ''
    for (const [sessionId, lines] of notes) {
      await this.options.sessions.inject(sessionId, meanwhileNote(lines)).catch((error: unknown) => this.log('warn', `nanomuse sync: context not injected: ${message(error)}`))
    }
    if (applied > 0) {
      this.log('info', `nanomuse sync: ${applied} change(s) from the account’s other devices`)
      this.changed()
    }
    return applied
  }

  private async applyConversation(c: WireConversation): Promise<void> {
    let sessionId = this.sessionOf(c.cid)
    if (sessionId && this.state.clouds[sessionId] === 'local') return
    if (sessionId && sessionId in (this.state.pendingArchives ?? {})) c = { ...c, archived: this.state.pendingArchives![sessionId] === true }
    if (c.deleted) {
      delete this.state.conversations[c.cid]
      if (sessionId) {
        // the harness has no delete: the session stops syncing under that id, and the chats column archives it
        delete this.state.cids[sessionId]
        delete this.state.titles[sessionId]
        // the chat is gone from the account, so what stays here is a local chat (C11)
        this.state.clouds[sessionId] = 'local'
        if (sessionId !== this.state.mainSession && !this.state.toArchive.includes(sessionId)) this.state.toArchive.push(sessionId)
      }
      return
    }
    // side chats off (C9): a side conversation from elsewhere is not a chat here, and one that was stays as it is
    if (c.kind === 'side' && !this.state.sideChats && this.catalogSupported !== true) return
    if (c.archived === false && sessionId) this.state.toArchive = this.state.toArchive.filter(id => id !== sessionId)
    const known = this.state.conversations[c.cid]
    this.state.conversations[c.cid] = {
      kind: c.kind,
      title: c.title || known?.title || '',
      device: known?.device || c.device,
      deviceName: known?.deviceName || c.device_name,
      createdAt: known?.createdAt || c.created_at,
      updatedAt: Math.max(known?.updatedAt ?? 0, c.updated_at),
      archived: c.archived,
    }
    if (c.archived && sessionId && sessionId !== this.state.mainSession && !this.state.toArchive.includes(sessionId)) this.state.toArchive.push(sessionId)
    if (!sessionId) {
      if (c.kind === 'main' && this.state.mainSession) {
        // Identifiers: the account's main conversation already exists — our main chat adopts its id
        // (ours was never accepted as main, or the relay would have refused theirs: its lines are
        // still unpushed and go up under the adopted id)
        sessionId = this.state.mainSession
        this.state.cids[sessionId] = c.cid
        this.adopt(sessionId)
        if (c.title) this.state.titles[sessionId] = c.title
        else delete this.state.titles[sessionId]
        this.dirty.add(sessionId)
        this.pushSoon(500)
        this.log('info', 'nanomuse sync: the main chat adopts the account’s conversation id')
      } else {
        // one thread (C8): a conversation from elsewhere is a chat here from the moment it is known
        const title = c.title || (c.kind === 'main' ? 'Main chat' : 'New chat')
        try {
          sessionId = await this.options.sessions.create(title)
        } catch (error: unknown) {
          this.log('warn', `nanomuse sync: no session for a synced conversation: ${message(error)}`)
          return
        }
        this.state.cids[sessionId] = c.cid
        this.adopt(sessionId)
        this.state.titles[sessionId] = title
        if (c.kind === 'main') this.state.mainSession = sessionId
        // A messenger conversation is a chat here at once, with its first page of words: a
        // blank row that never pulled anything is not a chat a person can find, and the MAX
        // chat used to be exactly that on the computer. A side chat of another device stays
        // closed until the person opens it, as before.
        if (/^(MAX|Telegram) ·/.test(title)) {
          this.viewSelections.set(`sync-new:${sessionId}`, { ids: [sessionId], until: Date.now() + 45_000 })
        }
      }
      this.noteCloud(sessionId, c.device)
      if (c.archived && sessionId !== this.state.mainSession && !this.state.toArchive.includes(sessionId)) this.state.toArchive.push(sessionId)
      return
    }
    if (c.title && known && known.title !== c.title && this.state.titles[sessionId] !== c.title) {
      // renamed elsewhere: renamed here, and not pushed back
      this.state.titles[sessionId] = c.title
      await this.options.sessions.rename(sessionId, c.title).catch(() => undefined)
    }
    this.noteCloud(sessionId, c.device)
  }

  /** Mark a shared chat as syncing, preserving an explicit local pause. */
  private noteCloud(sessionId: string, from: string): void {
    if (!sessionId || sessionId === this.state.mainSession) return
    const mark = this.state.clouds[sessionId]
    if (mark === 'device' || mark === 'sync' || mark === 'local') return
    if (from && from === this.options.deviceId()) return
    this.state.clouds[sessionId] = 'sync'
  }

  /**
   * A state from before C11 (no `clouds`) marks the chats that are already in the account by who made
   * their conversation: another device's, a messenger's or the account's own reads `sync`, and this
   * device's own pushed one is the person's own cloud, `device`. Nothing that was in the cloud stops
   * being in it because of this change.
   */
  private reconcileClouds(): void {
    for (const [sessionId, cid] of Object.entries(this.state.cids)) {
      if (sessionId === this.state.mainSession || this.state.clouds[sessionId] !== undefined) continue
      const known = this.state.conversations[cid]
      if (!known) continue
      this.state.clouds[sessionId] = known.device && known.device !== this.options.deviceId() ? 'sync' : 'device'
    }
  }

  private async applyMessage(m: WireMessage, notes: Map<string, RemoteLine[]>): Promise<void> {
    const mappedSession = this.sessionOf(m.cid)
    if (mappedSession && this.state.clouds[mappedSession] === 'local') return
    if (m.deleted) {
      const kept = this.state.pulled[m.mid]
      if (kept) {
        if (!this.state.hidden.includes(m.mid)) this.state.hidden.push(m.mid)
        await this.options.sessions.forget(kept, m.mid).catch(() => undefined)
      }
      return
    }
    // once is enough: a row kept here already, pulled or pushed by this device, is not
    // appended twice. The mid, not the device, is the identity: after a reinstall the
    // same device must get its own rows back.
    if (this.state.pulled[m.mid]) return
    if (Object.values(this.state.mids).includes(m.mid)) return
    const sessionId = this.sessionOf(m.cid)
    // side chats off (C9): an explicitly side conversation stays where it was written. If
    // its cached metadata was cleared, the existing mapping still proves that this is the
    // main conversation; do not advance the cursor past its final reply.
    const kind = this.state.conversations[m.cid]?.kind
    if (this.catalogSupported !== true && !this.state.sideChats && (kind === 'side' || (kind !== 'main' && sessionId !== this.state.mainSession))) return
    // the reply arrived: that device is not working on it any more; another device's line stays
    if (m.role === 'assistant') this.clearWorking(m.cid, m.device)
    if (!sessionId) return
    const line: RemoteLine = { mid: m.mid, role: m.role, text: m.text, at: m.created_at * 1000, device: m.device, deviceName: m.device_name }
    if (m.role === 'user' && this.options.sessions.promote) {
      const promoted = await this.options.sessions.promote(sessionId, line, this.state.titles[sessionId] ?? "").catch(() => false)
      if (promoted) {
        this.state.pulled[m.mid] = sessionId
        return
      }
    }
    try {
      await this.options.sessions.keep(sessionId, line)
      this.state.pulled[m.mid] = sessionId
      notes.set(sessionId, [...(notes.get(sessionId) ?? []), line])
    } catch (error: unknown) {
      this.log('warn', `nanomuse sync: a turn from ${m.device_name || 'another device'} did not reach its chat: ${message(error)}`)
    }
  }

  /** The session a conversation lives in here — one of the signed-in account's or nobody's (C10), never another account's. */
  private sessionOf(cid: string): string | undefined {
    for (const [sid, c] of Object.entries(this.state.cids)) if (c === cid && this.owns(sid)) return sid
    return undefined
  }

  // ---- errors -------------------------------------------------------------------------------

  /** Rule 7: network errors are silent; `sync_off` flips the switch here; a refused key pauses until the next sign-in. */
  private noteError(error: unknown): void {
    if (error instanceof RelayError) {
      if (error.code === 'sync_off') {
        this.state.enabled = false
        void this.save()
        this.log('info', 'nanomuse sync: turned off on another device; off here too')
        this.changed()
        return
      }
      if (error.status === 401) {
        this.paused = true
        this.lastError = 'signed_out'
        this.changed()
        return
      }
      this.lastError = error.code
    } else {
      this.lastError = message(error)
    }
    this.log('debug', `nanomuse sync: ${this.lastError}`)
  }

  get error(): string {
    return this.lastError
  }

  private changed(): void {
    this.rev += 1
    this.options.onChange?.()
  }

  private async save(): Promise<void> {
    await this.options.save(this.state).catch((error: unknown) => this.log('warn', `nanomuse sync: state not written: ${message(error)}`))
  }

  private log(level: 'info' | 'warn' | 'debug', text: string): void {
    this.options.log?.(level, text)
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
