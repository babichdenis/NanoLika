/**
 * The cloud on one chat (0.29), apart from the row that draws it: which of the four a chat is,
 * and what to say when the host refuses to change it. The main chat has no switch, a chat from
 * another device or a messenger has none either (the cloud holds its only copy), a side chat has
 * the person's own, and the rest are on this computer alone.
 */
import type { Translate } from './api.ts'
import type { Words } from './locales.ts'

/** The parts of the host's sync view this needs (`useSyncList` in `MuseChats.tsx`). */
export interface CloudList {
  /** Sessions the host holds a conversation for: whatever this device's switch says, the chat is in the cloud. */
  sessions: ReadonlySet<string>
  /** Side sessions the person put a cloud on here (0.29). */
  synced: ReadonlySet<string>
}

export type ChatCloud = 'main' | 'remote' | 'synced' | 'local'

/** Which of the four a row is: the main chat first, then the person's own switch, then the host's list of synced conversations. */
export function chatCloud(sync: CloudList, id: string, main: boolean): ChatCloud {
  if (main) return 'main'
  if (sync.synced.has(id)) return 'synced'
  if (sync.sessions.has(id)) return 'remote'
  return 'local'
}

/** Whether the row offers the switch at all: the two states the person works from here. */
export function switchable(cloud: ChatCloud): boolean {
  return cloud === 'synced' || cloud === 'local'
}

/**
 * The host's reasons for refusing a switch (`CloudRefusal` in `sync.ts`, `{ ok: false, reason }`),
 * in the person's words.
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
