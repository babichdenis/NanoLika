/** The per-chat cloud switch pauses exchange on this computer. Shared history and its
 * identifier remain in the account; explicit account deletion is a separate action.
 * The main account conversation stays shared.
 */
import type { Translate } from './api.ts'
import type { Words } from './locales.ts'

/** The parts of the host's sync view this needs (`useSyncList` in `MuseChats.tsx`). */
export interface CloudList {
  /** Sessions the host holds a conversation for: whatever this device's switch says, the chat is in the cloud. */
  sessions: ReadonlySet<string>
  /** Chats whose cloud the person turned on here (the host's `device` mark). */
  synced: ReadonlySet<string>
}

/** The host's own names (`CloudMark` in `sync.ts`) for the three a side chat can be, plus the main chat. */
export type ChatCloud = 'main' | 'device' | 'sync' | 'local'

/** What the row's cloud item does: put the cloud on, take it off, delete the conversation from the account whole, or nothing (the main chat). */
export type CloudAction = 'on' | 'off' | 'delete' | 'none'

/**
 * Which of the four a row is: the main chat first, then the person's own mark (`synced`), then the
 * host's list of conversations (`sync`): a conversation in the account that this device never
 * switched on came from elsewhere.
 */
export function chatCloud(sync: CloudList, id: string, main: boolean): ChatCloud {
  if (main) return 'main'
  if (sync.synced.has(id)) return 'device'
  if (sync.sessions.has(id)) return 'sync'
  return 'local'
}

/** The row's menu item: its words, the action behind it, and the tooltip that explains it. */
export interface CloudItem {
  action: CloudAction
  label: string
  /** Why the item is inert, or what the delete takes with it: the item's tooltip. */
  hint?: string | undefined
}

/** Main stays shared; either kind of side chat can pause locally. */
export function cloudItem(t: Translate, cloud: ChatCloud): CloudItem {
  switch (cloud) {
    case 'main':
      return { action: 'none', label: t('chUnsync'), hint: t('chCloudMain') }
    case 'device':
      return { action: 'off', label: t('chUnsync') }
    case 'sync':
      return { action: 'off', label: t('chUnsync') }
    case 'local':
      return { action: 'on', label: t('chSync') }
  }
}

/** The badge's tooltip: where the chat's copy lives, in one phrase. */
export function cloudSaid(t: Translate, cloud: ChatCloud): string {
  return cloud === 'local' ? t('chCloudOff') : t('chCloudOn')
}

/**
 * The host's reasons for refusing (`CloudRefusal` in `sync.ts`, `{ ok: false, reason }`), in the
 * person's words.
 */
export const CLOUD_REASONS: Record<string, Words> = {
  main: 'chCloudMain',
  'synced-from-cloud': 'chCloudRemote',
  'not-synced-account': 'chCloudNoSync',
  'unknown-session': 'chCloudFailed',
}

/** What to tell the person: our sentence for a reason we know, the host's own words for one we do not. */
export function cloudRefusal(t: Translate, reason: string | undefined): string {
  const known = reason ? CLOUD_REASONS[reason] : undefined
  if (known !== undefined) return t(known)
  return reason ? t('failed', { message: reason }) : t('chCloudFailed')
}
