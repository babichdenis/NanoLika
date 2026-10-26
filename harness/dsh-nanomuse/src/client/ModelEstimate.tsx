import { createElement as h, useEffect, useState, type ReactNode } from 'react'
import { call, type Translate } from './api.ts'
export interface CatalogueModel {
  id: string
  name: string
  kind: string
  context_length?: number
  max_output?: number
  commerce?: {
    discount_pct: number
    effective_rub: Record<string, number>
    controls: { key: string; label: string; type: string; default: string | number; options?: string[] }[]
  }
}
export function ModelEstimate({ model, t }: { model: CatalogueModel; t: Translate }): ReactNode {
  const [open, setOpen] = useState(false)
  const [params, setParams] = useState<Record<string, string | number>>(() => Object.fromEntries((model.commerce?.controls ?? []).map(c => [c.key, c.default])))
  const [result, setResult] = useState('')
  useEffect(() => {
    if (!open) return
    let alive = true
    setResult(t('mpEstimateLoading'))
    const timer = setTimeout(() => {
      void call<{ total_rub: number }>('model-quote', { model: model.id, ...params })
        .then(data => { if (alive) setResult(new Intl.NumberFormat(undefined, { style: 'currency', currency: 'RUB', maximumFractionDigits: 6 }).format(data.total_rub)) })
        .catch(() => { if (alive) setResult(t('mpEstimateError')) })
    }, 250)
    return () => { alive = false; clearTimeout(timer) }
  }, [open, params, model.id])
  return h('details', { className: 'nm-model-estimate', onToggle: (e: { currentTarget: HTMLDetailsElement }) => setOpen(e.currentTarget.open) },
    h('summary', null, t('mpEstimate')),
    open ? h('div', null,
      model.context_length ? h('p', null, `${t('mpContext')}: ${model.context_length.toLocaleString()}`) : null,
      (model.commerce?.controls ?? []).map(c => h('label', { key: c.key }, c.label,
        c.type === 'select'
          ? h('select', { value: params[c.key], onChange: (e: { currentTarget: HTMLSelectElement }) => setParams(p => ({ ...p, [c.key]: e.currentTarget.value })) }, (c.options ?? []).map(v => h('option', { key: v, value: v }, v)))
          : h('input', { type: 'number', min: 0, value: params[c.key], onChange: (e: { currentTarget: HTMLInputElement }) => setParams(p => ({ ...p, [c.key]: Number(e.currentTarget.value) })) }))),
      h('output', { 'aria-live': 'polite' }, result), h('small', null, t('mpEstimateNote'))) : null)
}
