/**
 * The preview pane's content, held outside any chat: the agent (or a click on a
 * link) asks for something to be shown beside the conversation — a page by URL,
 * inline HTML, a picture or a PDF — and the pane renders it. One small store,
 * read with `useSyncExternalStore`, written by `showPreview` / `hidePreview`.
 */
import { useSyncExternalStore } from 'react'
import { PREVIEW_PANEL } from './panels.ts'
import { peekWin, win } from './win.ts'

export type PreviewKind = 'url' | 'html' | 'image' | 'pdf'

export interface Preview {
  kind: PreviewKind
  title?: string
  url?: string
  html?: string
  src?: string
}

let state: Preview | null = null
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function usePreview(): Preview | null {
  return useSyncExternalStore(subscribe, () => state, () => state)
}

export function showPreview(p: Preview): void {
  state = p
  for (const listener of listeners) listener()
  win.split(PREVIEW_PANEL)
}

export function hidePreview(): void {
  state = null
  for (const listener of listeners) listener()
  if (peekWin().split === PREVIEW_PANEL) win.split(null)
}
