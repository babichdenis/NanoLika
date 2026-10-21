/**
 * A Workspace row in the harness's own sidebar, as far as a right-click needs to know it:
 * the core's browser marks each group header with `data-row-key="workspace:<id>"`, and its
 * own rename and delete live behind a hover ellipsis, so the row itself is the handle we
 * take (no DOM class names, which the core's CSS modules hash per build).
 */

/** The prefix the core puts in a Workspace group's `data-row-key`. */
export const WORKSPACE_ROW_PREFIX = 'workspace:'

export interface WorkspaceHit {
  readonly id: string
  /** The row element, for anchoring anything beside it. */
  readonly row: Element
}

/**
 * The Workspace a point landed on.
 * @param target - the event target, whatever it is (text node, SVG, nothing at all).
 * @returns the Workspace id and its row, or null when the point is not on one.
 */
export function workspaceIdOf(target: unknown): WorkspaceHit | null {
  if (typeof Element === 'undefined' || !(target instanceof Element)) return null
  const row = target.closest(`[data-row-key^="${WORKSPACE_ROW_PREFIX}"]`)
  if (row === null) return null
  return workspaceOfRow(row)
}

/**
 * The Workspace a row element stands for.
 * @param row - an element that may be a Workspace group header.
 * @returns the Workspace id and the row, or null when the element is something else.
 */
export function workspaceOfRow(row: Element | null): WorkspaceHit | null {
  if (row === null) return null
  const key = row.getAttribute('data-row-key') ?? ''
  if (!key.startsWith(WORKSPACE_ROW_PREFIX)) return null
  const id = key.slice(WORKSPACE_ROW_PREFIX.length)
  return id === '' ? null : { id, row }
}

/** What the sidebar may do to a Workspace: the client entry hands in the core's own service. */
export interface WorkspaceAdmin {
  /** The Workspace's current title, or an empty string when it is not known. */
  titleOf(id: string): string
  rename(id: string, title: string): Promise<unknown>
  delete(id: string): Promise<unknown>
}
