/**
 * The model picker of Settings → Models and Settings → Media (the Models contract, same on
 * the phones): a button that reads `<provider> · <model>` and opens a panel under it with the
 * fixed rows first (*Automatic*, *Off*), a search field once the lists are long, then one
 * group per provider, each folded to a few rows with *Show {n} more* at its foot
 * (`model-list.ts` decides what is shown). A native `<select>` can neither fold nor filter,
 * and OpenRouter or SiliconFlow list hundreds of models. Esc, a click outside and a pick
 * close it; the rows are buttons, so Tab reaches them, and the arrows walk them.
 */
import { createElement as h, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { call, type Translate } from './api.ts'
import { settingsBus } from './bus.ts'
import { ModelEstimate, type CatalogueModel } from './ModelEstimate.tsx'
import { IconCheck, IconChevronDown, IconSearch } from './icons.tsx'
import { hasSearch, layoutGroups, type Current, type ModelGroup, type ModelRow } from './model-list.ts'

/** A row before the groups: *Automatic*, *Off*. */
export interface PickerHead {
  value: string
  label: string
}

export interface ModelPickerProps<R extends ModelRow = ModelRow> {
  t: Translate
  /** The row's title, for the button's accessible name. */
  label: string
  /** What the button reads: `<provider> · <model>`, or the head row's label. */
  text: string
  heads?: PickerHead[]
  /** The selected head row's value, when the picker stands on one. */
  headValue?: string
  groups: readonly ModelGroup<R>[]
  /** The chosen model, when the picker stands on one. */
  current?: Current
  /** Other models in use elsewhere (a library picker spans the chat, picture and clip slots). */
  currentIds?: readonly string[]
  disabled?: boolean
  testId?: string
  onHead?(value: string): void
  onPick(group: string, row: R): void
  /** How a row is written; the row's name by default. */
  rowText?(row: R, info?: CatalogueModel): ReactNode
  /**
   * Which catalogue models belong to this picker. The ones the tariff keeps closed are
   * listed under the open ones as a preview with their price and a lock: the cabinet does
   * the same, so the app and the web agree on what a bigger plan would give.
   */
  catalogueFilter?(model: CatalogueModel): boolean
}

const PANEL_WIDTH = 620
const PANEL_MAX_HEIGHT = 560
const MODEL_KINDS = [
  ['chat', 'mpKindText'], ['image', 'mpKindImages'], ['video', 'mpKindVideo'], ['audio', 'mpKindAudio'],
  ['embedding', 'mpKindEmbeddings'], ['rerank', 'mpKindRerank'],
] as const
const KIND_STORE = 'nm.mp.kind'
const KIND_VALUES = ['all', ...MODEL_KINDS.map(([value]) => value)]

/** The last kind tab a person used, so every picker opens where they left off. */
function rememberedKind(): string {
  try {
    const value = localStorage.getItem(KIND_STORE) || ''
    return KIND_VALUES.includes(value) ? value : 'all'
  } catch {
    return 'all'
  }
}

function rememberKind(value: string): void {
  try { localStorage.setItem(KIND_STORE, value) } catch { /* a private window says no */ }
}

function cataloguePrice(model: CatalogueModel | undefined): number {
  if (!model) return Number.POSITIVE_INFINITY
  const rates = model.commerce?.effective_rub ?? {}
  if (model.kind === 'chat') return Number(rates.input ?? 0) + Number(rates.output ?? 0) * 0.512
  if (model.kind === 'image') return Number(rates.image ?? Number.POSITIVE_INFINITY)
  if (model.kind === 'video') return Number(rates.second ?? Number.POSITIVE_INFINITY)
  return Number(rates.speech ?? rates.minute ?? Number.POSITIVE_INFINITY)
}

function visiblePrice(model: CatalogueModel | undefined, t: Translate): string | undefined {
  if (!model) return undefined
  const rates = model.commerce?.effective_rub ?? {}
  const fmt = (value: unknown) => Number(value) > 0
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(Number(value))
    : undefined
  if (model.free) return t('mpFreeModel')
  if (model.kind === 'chat') {
    const input = fmt(rates.input), output = fmt(rates.output)
    return input || output ? `${t('mpPriceMillion')}: ${input ?? '—'} / ${output ?? '—'}` : undefined
  }
  const media = model.kind === 'image' ? fmt(rates.image) : model.kind === 'video' ? fmt(rates.second) : fmt(rates.speech ?? rates.minute)
  if (!media) return undefined
  const unit = model.kind === 'image' ? t('mpPriceImage') : model.kind === 'video' ? t('mpPriceSecond') : rates.speech ? t('mpPriceSpeech') : t('mpPriceMinute')
  return `${media} ${unit}`
}

function place(anchor: HTMLElement): Record<string, number> {
  const rect = anchor.getBoundingClientRect()
  const below = rect.bottom + PANEL_MAX_HEIGHT + 12 < window.innerHeight || rect.top < window.innerHeight / 2
  const width = Math.min(PANEL_WIDTH, window.innerWidth - 16)
  const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))
  return {
    left,
    width,
    ...(below ? { top: rect.bottom + 4, maxHeight: Math.min(PANEL_MAX_HEIGHT, window.innerHeight - rect.bottom - 12) } : { bottom: window.innerHeight - rect.top + 4, maxHeight: Math.min(PANEL_MAX_HEIGHT, rect.top - 12) }),
  }
}

export function ModelPicker<R extends ModelRow>({ t, label, text, heads = [], headValue, groups, current, currentIds, disabled, testId, onHead, onPick, rowText, catalogueFilter }: ModelPickerProps<R>): ReactNode {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [context, setContext] = useState(0)
  const [kind, setKind] = useState('all')
  const [sort, setSort] = useState('recommended')
  // *Favourites* is a tab like the kinds, not the default: a person opens the picker to see
  // the menu, and the starred rows are one click away, never the only thing in sight.
  const [favouritesOnly, setFavouritesOnly] = useState(false)
  const [collapsedKinds, setCollapsedKinds] = useState<Set<string>>(() => new Set())
  const [favourites, setFavourites] = useState<string[]>([])
  const [catalogue, setCatalogue] = useState<CatalogueModel[]>([])
  const [catalogueError, setCatalogueError] = useState('')
  const writes = useRef<Promise<unknown>>(Promise.resolve())
  useEffect(() => {
    if (!open) return
    setKind(rememberedKind())
    let alive = true
    call<{ models: CatalogueModel[]; shelf: { favourites?: string[] } }>('model-catalogue')
      .then(data => { if (alive) { setCatalogue(data.models); setFavourites(data.shelf.favourites ?? []); setCatalogueError('') } })
      .catch(() => { if (alive) { setCatalogue([]); setFavourites([]); setFavouritesOnly(false); setCatalogueError(t('mpCatalogueUnavailable')) } })
    return () => { alive = false }
  }, [open])
  const chooseKind = (value: string) => {
    setKind(value)
    rememberKind(value)
  }
  const star = (id: string) => {
    const next = favourites.includes(id) ? favourites.filter(x => x !== id) : [...favourites, id]
    setFavourites(next)
    writes.current = writes.current.catch(() => {}).then(() => call('model-favourites', { favourites: next }))
      .catch(() => setCatalogueError(t('mpFavouriteError')))
  }
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [style, setStyle] = useState<Record<string, number>>({})
  const button = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const searchable = hasSearch(groups)

  const close = useCallback((refocus = false) => {
    setOpen(false)
    setQuery('')
    setExpanded(new Set())
    if (refocus) button.current?.focus()
  }, [])

  /** The wallet is where a bigger plan and its models live; the picker only points there. */
  const openPlans = () => { settingsBus.openSection?.('nanomuse-wallet') }

  // where the panel sits: under the button, over it when the screen ends; again on scroll and resize
  useLayoutEffect(() => {
    if (!open || !button.current) return
    const update = () => { if (button.current) setStyle(place(button.current)) }
    update()
    window.addEventListener('resize', update)
    document.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      document.removeEventListener('scroll', update, true)
    }
  }, [open])

  // Esc and a click outside close it
  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (panel.current?.contains(target) || button.current?.contains(target)) return
      close()
    }
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close(true)
      }
    }
    document.addEventListener('pointerdown', onPointer, true)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('pointerdown', onPointer, true)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open, close])

  // focus lands on the search field when there is one, else on the selected row, else the first
  useEffect(() => {
    if (!open || !panel.current) return
    const first = panel.current.querySelector<HTMLElement>('input') ?? panel.current.querySelector<HTMLElement>('[aria-selected="true"]') ?? panel.current.querySelector<HTMLElement>('[data-nm-mp-row]')
    first?.focus()
  }, [open])

  const walk = (event: { key: string; preventDefault(): void; target: EventTarget | null }) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    const rows = [...(panel.current?.querySelectorAll<HTMLElement>('[data-nm-mp-row]') ?? [])]
    if (!rows.length) return
    event.preventDefault()
    const at = rows.indexOf(event.target as HTMLElement)
    const next = event.key === 'ArrowDown' ? (at < 0 ? 0 : Math.min(rows.length - 1, at + 1)) : (at < 0 ? rows.length - 1 : Math.max(0, at - 1))
    rows[next]?.focus()
  }

  const kindOf = (row: R): string => catalogue.find(model => model.id === row.id)?.kind || 'chat'
  const allowedIds = new Set(groups.flatMap(group => group.rows.map(row => row.id)))
  // The tariff's closed models are a preview only: never pickable, priced and locked. They
  // are left out of *Favourites* — a star on something the plan does not open would mislead.
  const lockedAll = catalogueFilter
    ? catalogue.filter(model => model.plan_locked === true && catalogueFilter(model) && !allowedIds.has(model.id))
    : []
  const locked = favouritesOnly ? [] : lockedAll
  const favouriteCount = groups.reduce((n, group) => n + (group.key === 'nanomuse' ? group.rows.filter(row => favourites.includes(row.id)).length : 0), 0)
  // Counts describe the whole menu of the picker, the locked preview included. They do not
  // change when one kind is selected, so the other tabs remain useful for navigation.
  const kindCounts = MODEL_KINDS.map(([value, label]) => ({
    value,
    label: t(label),
    count: groups.reduce((n, group) => n + group.rows.filter(row => kindOf(row) === value && (!favouritesOnly || (group.key === 'nanomuse' && favourites.includes(row.id)))).length, 0)
      + (favouritesOnly ? 0 : lockedAll.filter(model => model.kind === value).length),
  })).filter(item => item.count > 0)
  const activeKind = kind === 'all' || kindCounts.some(item => item.value === kind) ? kind : 'all'
  const total = groups.reduce((n, group) => n + group.rows.filter(row => !favouritesOnly || (group.key === 'nanomuse' && favourites.includes(row.id))).length, 0) + locked.length
  const filtered = groups.map(group => ({ ...group, rows: group.rows.filter(row => {
    const info = catalogue.find(m => m.id === row.id)
    const rowKind = info?.kind || 'chat'
    return (activeKind === 'all' || rowKind === activeKind) && (!context || (info?.context_length ?? 0) >= context) && (!favouritesOnly || (group.key === 'nanomuse' && favourites.includes(row.id)))
  }) }))
  const laidOut = layoutGroups(filtered, { ...(current ? { current } : {}), query, expanded })
  const shown = laidOut.map(group => ({ ...group, rows: sort === 'recommended' ? group.rows : [...group.rows].sort((a, b) => {
    const left = catalogue.find(model => model.id === a.id), right = catalogue.find(model => model.id === b.id)
    if (sort === 'price') return cataloguePrice(left) - cataloguePrice(right) || a.name.localeCompare(b.name)
    if (sort === 'context') return (right?.context_length ?? 0) - (left?.context_length ?? 0) || a.name.localeCompare(b.name)
    return a.name.localeCompare(b.name)
  }) }))
  const searching = query.trim().length > 0
  const sections = kindCounts.map(item => ({
    ...item,
    groups: shown.map(group => ({
      ...group,
      rows: group.rows.filter(row => kindOf(row) === item.value),
    })).filter(group => group.rows.length > 0),
    locked: activeKind === 'all' || activeKind === item.value ? locked.filter(model => model.kind === item.value) : [],
  })).filter(item => item.groups.length > 0 || item.locked.length > 0)

  const row = (key: string, selected: boolean, content: ReactNode, onClick: () => void, extra: Record<string, unknown> = {}) =>
    h('button', { key, type: 'button', role: 'option', 'aria-selected': selected, 'data-nm-mp-row': '', className: `nm-mp-row${selected ? ' nm-selected' : ''}`, onClick, onKeyDown: walk, ...extra },
      h('span', { className: 'nm-mp-row-text' }, content),
      selected ? h(IconCheck, { size: 15 }) : null)

  /** The tariff's closed models: a price, a lock and no pick — the same tease as the cabinet. */
  const lockedGroup = (models: readonly CatalogueModel[]) => h('div', { key: 'locked', className: 'nm-mp-group nm-mp-locked-group', role: 'group', 'aria-label': t('mpLockedGroup') },
    h('div', { className: 'nm-mp-group-label' }, t('mpLockedGroup')),
    models.map(model => {
      const price = visiblePrice(model, t)
      return h('div', { key: `locked:${model.id}`, className: 'nm-mp-model nm-mp-model-locked' },
        h('div', { className: 'nm-mp-row nm-mp-locked-row', 'aria-disabled': 'true', title: model.id },
          h('span', { className: 'nm-mp-row-text' }, model.name),
          h('span', { className: 'nm-mp-lock-badge' }, `🔒 ${t('mpPlanLocked')}`)),
        h('div', { className: 'nm-mp-meta' },
          model.vendor ? h('span', null, model.vendor) : null,
          model.context_length ? h('span', null, `${t('mpContext')} ${model.context_length.toLocaleString()}`) : null,
          price ? h('span', null, price) : null,
          model.commerce?.discount_pct ? h('span', { className: 'nm-model-discount' }, `−${model.commerce.discount_pct}%`) : null))
    }))

  return h('div', { className: 'nm-mp' },
    h('button', {
      ref: button,
      type: 'button',
      className: 'nm-field nm-select nm-mp-btn',
      disabled,
      'aria-label': label,
      'aria-haspopup': 'listbox',
      'aria-expanded': open,
      'data-testid': testId,
      title: text,
      onClick: () => (open ? close() : setOpen(true)),
    }, h('span', { className: 'nm-mp-btn-text' }, text), h(IconChevronDown, { size: 16 })),
    open
      ? h('div', { ref: panel, className: 'nm-mp-panel', role: 'listbox', 'aria-label': label, style, 'data-testid': testId ? `${testId}-panel` : undefined },
          heads.length ? h('div', { className: 'nm-mp-heads' }, heads.map((head) => row(`head:${head.value}`, headValue === head.value, head.label, () => { onHead?.(head.value); close(true) }))) : null,
          searchable
            ? h('label', { className: 'nm-mp-search' },
                h(IconSearch, { size: 15 }),
                h('input', { type: 'search', value: query, placeholder: t('mpSearch'), 'aria-label': t('mpSearch'), autoComplete: 'off', spellCheck: false, onChange: (e: { currentTarget: HTMLInputElement }) => setQuery(e.currentTarget.value), onKeyDown: walk }))
            : null,
          h('div', { className: 'nm-mp-filters' },
            kindCounts.length > 1 || groups.some(group => group.key === 'nanomuse') || lockedAll.length
              ? h('div', { className: 'nm-mp-kinds', role: 'group', 'aria-label': t('mpKindGroup') },
                  kindCounts.length > 1 ? h('button', { type: 'button', className: 'nm-pill nm-pill-sm', 'aria-pressed': activeKind === 'all', onClick: () => chooseKind('all') }, `${t('mpAllKinds')} · ${total}`) : null,
                  kindCounts.map(item => h('button', { key: item.value, type: 'button', className: 'nm-pill nm-pill-sm', 'aria-pressed': activeKind === item.value, onClick: () => chooseKind(item.value) }, `${item.label} · ${item.count}`)),
                  groups.some(group => group.key === 'nanomuse') || lockedAll.length
                    ? h('button', {
                        type: 'button', className: 'nm-pill nm-pill-sm', 'aria-pressed': favouritesOnly,
                        disabled: favouriteCount === 0 && !favouritesOnly,
                        onClick: () => setFavouritesOnly(!favouritesOnly),
                      }, `★ ${t('mpFavourites')}${favouriteCount ? ` · ${favouriteCount}` : ''}`)
                    : null)
              : null,
            h('select', { 'aria-label': t('mpContext'), value: context, onChange: (e: { currentTarget: HTMLSelectElement }) => setContext(Number(e.currentTarget.value)) },
              [0, 128000, 200000, 1000000].map(n => h('option', { key: n, value: n }, n ? `${n / 1000}K+` : t('mpAnyContext')))),
            h('label', { className: 'nm-mp-sort' }, t('mpSort'), h('select', { value: sort, onChange: (e: { currentTarget: HTMLSelectElement }) => setSort(e.currentTarget.value) },
              [['recommended', t('mpRecommended')], ['price', t('mpLowestPrice')], ['context', t('mpLargestContext')], ['name', t('mpName')]].map(([value, title]) => h('option', { key: value, value }, title))))),
          catalogueError ? h('p', { role: 'status' }, catalogueError) : null,
          h('div', { className: 'nm-mp-list' },
            sections.length === 0 ? h('div', { className: 'nm-mp-none' }, t(favouritesOnly ? 'mpNoFavourites' : 'mpNoMatch')) : null,
            sections.map(section => h('section', { key: section.value, className: 'nm-mp-kind' },
              h('button', {
                type: 'button', className: 'nm-mp-kind-head',
                'aria-expanded': searching || !collapsedKinds.has(section.value),
                onClick: () => setCollapsedKinds(prev => {
                  const next = new Set(prev)
                  if (next.has(section.value)) next.delete(section.value)
                  else next.add(section.value)
                  return next
                }),
              }, `${searching || !collapsedKinds.has(section.value) ? '▾' : '▸'} ${section.label} · ${section.count}`),
              searching || !collapsedKinds.has(section.value) ? [
                ...section.groups.map(group =>
                h('div', { key: group.key, className: 'nm-mp-group', role: 'group', 'aria-label': group.label },
                  h('div', { className: 'nm-mp-group-label' }, group.label),
                  group.key === 'nanomuse' ? h('div', { className: 'nm-mp-group-note' }, t('mpCloudBilling')) : null,
                  group.rows.map((r) => {
                    const info = group.key === 'nanomuse' ? catalogue.find(m => m.id === r.id) : undefined
                    return h('div', { key: `${group.key}:${r.id}`, className: 'nm-mp-model' },
                      row(`${group.key}:${r.id}`, (current?.group === group.key && current.id === r.id) || Boolean(currentIds?.includes(r.id)), rowText ? rowText(r, info) : (info?.name || r.name), () => { onPick(group.key, r); close(true) }, { title: r.id }),
                      info && (info.free || info.context_length || info.max_output || info.vendor || visiblePrice(info, t) || info.commerce?.discount_pct) ? h('div', { className: 'nm-mp-meta' },
                        info.vendor ? h('span', null, info.vendor) : null,
                        info.context_length ? h('span', null, `${t('mpContext')} ${info.context_length.toLocaleString()}`) : null,
                        visiblePrice(info, t) ? h('span', { className: info.free ? 'nm-model-free' : undefined }, visiblePrice(info, t)) : null,
                        info.max_output ? h('span', null, `${t('mpMaxOutput')} ${info.max_output.toLocaleString()}`) : null,
                        info.commerce?.discount_pct ? h('span', { className: 'nm-model-discount' }, `−${info.commerce.discount_pct}%`) : null) : null,
                      info ? h('button', { type: 'button', className: 'nm-mp-favourite', 'aria-label': `${t('mpFavourites')}: ${r.name}`, 'aria-pressed': favourites.includes(r.id), onClick: () => star(r.id) }, favourites.includes(r.id) ? '★' : '☆') : null,
                      info ? h(ModelEstimate, { model: info, t }) : null)
                  }),
                  group.more > 0
                    ? h('button', { type: 'button', className: 'nm-mp-more', 'data-nm-mp-row': '', onKeyDown: walk, onClick: () => setExpanded((prev) => new Set([...prev, group.key])) }, t('mpShowMore', { n: group.more }))
                    : null)),
                section.locked.length ? lockedGroup(section.locked) : null,
              ] : null)),
          lockedAll.length ? h('button', { type: 'button', className: 'nm-mp-manage', onClick: openPlans }, t('mpPlansNote', { n: lockedAll.length })) : null
        ))
      : null)
}
