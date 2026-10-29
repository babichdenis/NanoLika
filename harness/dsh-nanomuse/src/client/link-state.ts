/** What "connected" means for a QR link: a chat that was not on the account before it. */
import type { LinkChannel } from './api.ts'

/**
 * One chat as the relay lists it. A chat is the pair of its messenger and its identifier;
 * `linkedAt` is the relay's own moment and tells a re-link from the row that was already there.
 */
export function linkKey(row: LinkChannel): string {
  return `${row.channel}\u0000${row.identifier}\u0000${row.linkedAt}`
}

/**
 * The chat this QR brought, or undefined while nothing new has arrived.
 *
 * A messenger can hold several chats, and the account's own chat is listed too, so "a
 * Telegram row exists" says nothing about the code on screen. Only a row that was not in
 * `known` when the code was made is the code's result — the same rule for the desktop app
 * and the page, so a tab switch or an old row never passes for a new connection.
 */
export function newLink(known: Iterable<string>, rows: LinkChannel[], channel: string): LinkChannel | undefined {
  const seen = new Set(known)
  return rows.find((row) => row.channel === channel && !seen.has(linkKey(row)))
}
