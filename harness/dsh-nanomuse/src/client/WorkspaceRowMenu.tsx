/**
 * A right-click on a Workspace in the harness's sidebar. The core draws those groups with
 * rename and delete behind an ellipsis that only appears on hover, and gives the row no
 * context menu at all, so the two actions a person looks for there live here instead: rename
 * it, or close it (the core's delete takes the folder off the list, it does not touch the
 * files or the chats inside it). Escape, a press outside or a choice closes the menu.
 */
import { createElement as h, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Translate } from './api.ts'
import { IconBan, IconCheck, IconPencil } from './icons.tsx'
import { workspaceIdOf, type WorkspaceAdmin } from './workspace-row.ts'

export interface WorkspaceRowMenuProps {
  t: Translate
  /** What the client entry's Workspace service can do, handed in so this file holds no services. */
  admin: WorkspaceAdmin
}

type Step = 'menu' | 'rename' | 'close'
interface Open {
  readonly id: string
  readonly title: string
  readonly x: number
  readonly y: number
  readonly step: Step
}

/** The words a refusal carries, whatever shape it arrives in. */
function wordsOf(error: unknown): string {
  if (typeof error === 'string') return error
  const message = (error as { message?: unknown })?.message
  return typeof message === 'string' ? message : String(error)
}

export function WorkspaceRowMenu({ t, admin }: WorkspaceRowMenuProps): ReactNode {
  const [open, setOpen] = useState<Open | null>(null)
  const [draft, setDraft] = useState('')
  const [failure, setFailure] = useState('')
  const menu = useRef<HTMLDivElement>(null)

  // The right-click itself: on a Workspace group and nowhere else.
  useEffect(() => {
    const onContextMenu = (event: MouseEvent) => {
      const hit = workspaceIdOf(event.target)
      if (hit === null) return
      event.preventDefault()
      const title = admin.titleOf(hit.id)
      setDraft(title)
      setFailure('')
      setOpen({ id: hit.id, title, x: event.clientX, y: event.clientY, step: 'menu' })
    }
    document.addEventListener('contextmenu', onContextMenu, true)
    return () => document.removeEventListener('contextmenu', onContextMenu, true)
  }, [admin])

  // Everything that closes it: Escape, a press anywhere else, another right-click.
  useEffect(() => {
    if (open === null) return
    const onPointer = (event: PointerEvent) => {
      if (menu.current?.contains(event.target as Node) === true) return
      setOpen(null)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(null)
    }
    document.addEventListener('pointerdown', onPointer, true)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer, true)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (open === null) return null

  const step = (next: Step, title = open.title) => { setFailure(''); setOpen({ ...open, title, step: next }) }
  const save = async () => {
    const next = draft.trim()
    if (next === '' || next === open.title) { setOpen(null); return }
    try {
      await admin.rename(open.id, next)
      setOpen(null)
    } catch (error) {
      setFailure(wordsOf(error))
    }
  }
  const close = async () => {
    try {
      await admin.delete(open.id)
      setOpen(null)
    } catch (error) {
      setFailure(wordsOf(error))
    }
  }

  const width = 260
  const height = open.step === 'menu' ? 120 : 170
  const clamp = (value: number, room: number) => Math.max(8, Math.min(value, room))
  const style = typeof window === 'undefined'
    ? undefined
    : { left: clamp(open.x, window.innerWidth - width), top: clamp(open.y, window.innerHeight - height) }

  const item = (id: string, label: string, icon: ReactNode, onSelect: () => void, danger = false) =>
    h('button', {
      key: id,
      type: 'button',
      role: 'menuitem',
      className: `nm-menu-item${danger ? ' nm-danger' : ''}`,
      onClick: onSelect,
    }, icon, h('span', { className: 'nm-menu-item-label' }, label))

  const body = open.step === 'menu'
    ? [
        item('rename', t('wsRename'), h(IconPencil, { size: 15 }), () => { step('rename') }),
        item('close', t('wsClose'), h(IconBan, { size: 15 }), () => { step('close') }, true),
      ]
    : open.step === 'rename'
      ? [
          h('div', { key: 'ask', className: 'nm-ws-ask' }, t('wsRenameAsk')),
          h('input', {
            key: 'field',
            className: 'nm-field nm-ws-input',
            value: draft,
            autoFocus: true,
            spellCheck: false,
            'aria-label': t('wsRename'),
            onChange: (event: { target: { value: string } }) => { setDraft(event.target.value) },
            onKeyDown: (event: { key: string; preventDefault(): void }) => {
              if (event.key === 'Enter') { event.preventDefault(); void save() }
            },
          }),
          h('div', { key: 'row', className: 'nm-ws-row' },
            h('button', { type: 'button', className: 'nm-pill', onClick: () => { void save() } }, h(IconCheck, { size: 14 }), h('span', null, t('wsSave'))),
            h('button', { type: 'button', className: 'nm-pill nm-pill-ghost', onClick: () => { setOpen(null) } }, h('span', null, t('wsCancel')))),
        ]
      : [
          h('div', { key: 'ask', className: 'nm-ws-ask' }, t('wsCloseAsk', { name: open.title })),
          h('div', { key: 'row', className: 'nm-ws-row' },
            h('button', { type: 'button', className: 'nm-pill nm-pill-danger', onClick: () => { void close() } }, h(IconBan, { size: 14 }), h('span', null, t('wsClose'))),
            h('button', { type: 'button', className: 'nm-pill nm-pill-ghost', onClick: () => { setOpen(null) } }, h('span', null, t('wsCancel')))),
        ]

  return h('div', { ref: menu, className: 'nm-menu nm-ws-menu', role: 'menu', style, 'aria-label': t('wsRename') },
    ...body,
    failure === '' ? null : h('div', { key: 'failure', className: 'nm-ws-failure', role: 'alert' }, failure))
}
