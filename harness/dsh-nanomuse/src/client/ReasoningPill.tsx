/**
 * The thinking-level control beside a model: a small pill that reads the level in use and
 * opens the levels the model documents, *default* first. It is the same control in the
 * composer and in Settings → Models, so a person meets one thing in both places.
 *
 * *Default* is not a level sent on the wire: it is the absence of one, and the provider
 * then uses whatever it uses by itself. The relay's menu says which level that is
 * (`reasoning.default`), and the pill's tooltip repeats it, so "по умолчанию" never hides
 * what actually happens.
 */
import { createElement as h, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Translate } from './api.ts'
import { IconCheck, IconChevronDown } from './icons.tsx'

const LABELS: Record<string, string> = {
  minimal: 'mlReasoningMinimal',
  low: 'mlReasoningLow',
  medium: 'mlReasoningMedium',
  high: 'mlReasoningHigh',
  xhigh: 'mlReasoningXhigh',
  max: 'mlReasoningMax',
}

/** The level's name in the person's language; an unknown level is written as it came. */
export function reasoningLabel(t: Translate, level: string): string {
  if (!level) return t('mlReasoningDefault')
  const key = LABELS[level] as Parameters<Translate>[0] | undefined
  return key ? t(key) : level
}

export interface ReasoningPillProps {
  t: Translate
  levels: readonly string[]
  /** The chosen level; '' means the provider's own default. */
  value: string
  /** What the provider uses when nothing is chosen (the menu's `reasoning.default`). */
  fallback?: string
  disabled?: boolean
  testId?: string
  onPick(level: string): void
}

function place(anchor: HTMLElement): Record<string, number> {
  const rect = anchor.getBoundingClientRect()
  const width = 210
  const height = 44 + 34 * 7
  const below = rect.bottom + height + 12 < window.innerHeight || rect.top < window.innerHeight / 2
  return {
    left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
    width,
    ...(below ? { top: rect.bottom + 6 } : { bottom: window.innerHeight - rect.top + 6 }),
    maxHeight: Math.min(360, below ? window.innerHeight - rect.bottom - 18 : rect.top - 18),
  }
}

export function ReasoningPill({ t, levels, value, fallback, disabled, testId, onPick }: ReasoningPillProps): ReactNode {
  const [open, setOpen] = useState(false)
  const [style, setStyle] = useState<Record<string, number>>({})
  const button = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const title = fallback && !value ? `${t('mlReasoning')}: ${t('mlReasoningDefault')} (${reasoningLabel(t, fallback)})` : `${t('mlReasoning')}: ${reasoningLabel(t, value)}`

  useLayoutEffect(() => {
    if (!open || !button.current) return
    const update = (): void => { if (button.current) setStyle(place(button.current)) }
    update()
    window.addEventListener('resize', update)
    document.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      document.removeEventListener('scroll', update, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent): void => {
      const target = event.target as Node
      if (panel.current?.contains(target) || button.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setOpen(false)
        button.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointer, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('pointerdown', onPointer, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const pick = (level: string): void => {
    setOpen(false)
    button.current?.focus()
    if (level !== value) onPick(level)
  }
  const choices: ReadonlyArray<readonly [string, string]> = [
    ['', t('mlReasoningDefault')],
    ...levels.map((level): readonly [string, string] => [level, reasoningLabel(t, level)]),
  ]

  return h('span', { className: 'nm-reasoning' },
    h('button', {
      ref: button,
      type: 'button',
      className: 'nm-pill nm-pill-ghost nm-pill-sm nm-reasoning-pill',
      'data-testid': testId,
      'aria-haspopup': 'listbox',
      'aria-expanded': open,
      'aria-label': title,
      title,
      disabled,
      onClick: () => setOpen((prev) => !prev),
    }, h('span', { className: 'nm-reasoning-pill-text' }, reasoningLabel(t, value)), h(IconChevronDown, { size: 12 })),
    open
      ? createPortal(
          h('div', {
            ref: panel,
            className: 'nm-reasoning-panel',
            role: 'listbox',
            'aria-label': t('mlReasoning'),
            style,
          },
            choices.map(([level, label]) =>
              h('button', {
                key: level || 'default',
                type: 'button',
                role: 'option',
                'aria-selected': level === value,
                className: `nm-reasoning-option${level === value ? ' nm-selected' : ''}`,
                title: level ? undefined : t('mlReasoningHint'),
                onClick: () => pick(level),
              },
                h('span', null, label),
                level === value ? h(IconCheck, { size: 14 }) : null)),
            fallback && !value ? h('p', { className: 'nm-reasoning-note' }, `${t('mlReasoningDefault')}: ${reasoningLabel(t, fallback ?? '')}`) : null,
          ),
          document.body,
        )
      : null)
}
