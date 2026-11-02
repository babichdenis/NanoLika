/**
 * A picture opened to the size of the window, over the chat: the same store shape as
 * the preview pane, so a click on any picture (a messenger's, the relay's, the web's)
 * lands in one viewer with a way to keep it. One picture at a time.
 */
import { useSyncExternalStore } from 'react'

export interface ShownImage {
  url: string
  /** The file's own name, used when saving it. */
  name?: string
}

let shown: ShownImage | null = null
const listeners = new Set<() => void>()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useImageView(): ShownImage | null {
  return useSyncExternalStore(subscribe, () => shown, () => shown)
}

export function showImage(url: string, name?: string): void {
  if (!url) return
  shown = { url, ...(name ? { name } : {}) }
  for (const listener of listeners) listener()
}

export function hideImage(): void {
  shown = null
  for (const listener of listeners) listener()
}
