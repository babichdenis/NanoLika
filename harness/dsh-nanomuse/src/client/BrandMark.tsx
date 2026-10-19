/**
 * The app's own mark (docs/brand.md): the sage fairy wings on its white squircle tile with a hairline
 * edge, drawn inline from `assets/brand/nanomuse-icon.svg` so the bundle fetches nothing. It
 * stands for the app where the app speaks for itself — the sign-in pages, the permissions
 * page — never for the Muse: the face is the Muse, the mark is the app, and the mark never
 * goes in the chat. The tile stays warm ivory in the dark theme too (the brand's rule).
 */
import { createElement as h, useId, type ReactNode } from 'react'

/** The wings, in the tile's 100 × 100 coordinates (one filled path, the brand's gradient). */
const STROKE = 'M49 71 C18 67 9 28 18 20 C38 17 52 47 49 71 Z M51 69 C48 40 68 12 84 19 C94 38 73 64 51 69 Z M46 73 C25 84 13 71 18 59 C29 52 42 60 46 73 Z M54 73 C74 87 89 74 83 61 C70 54 60 63 54 73 Z'

export function BrandMark({ size = 72, className }: { size?: number; className?: string }): ReactNode {
  // one gradient per instance: two marks on one page must not share an id
  const id = `nm-mark-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  return h('svg', {
    width: size,
    height: size,
    viewBox: '0 0 100 100',
    className: `nm-brand-mark${className ? ` ${className}` : ''}`,
    role: 'img',
    'aria-label': 'NanoLika',
    focusable: false,
  },
    h('defs', null,
      h('linearGradient', { id, gradientUnits: 'userSpaceOnUse', x1: 12.6, y1: 50, x2: 87.8, y2: 50 },
        h('stop', { offset: 0, stopColor: '#52694e' }),
        h('stop', { offset: 1, stopColor: '#a8bfa4' }))),
    // the tile with its hairline edge, so it keeps its shape on a white page
    h('rect', { x: 0.3, y: 0.3, width: 99.4, height: 99.4, rx: 27, ry: 27, fill: '#F7F1E6', stroke: '#D9D9DE', strokeWidth: 0.6 }),
    h('path', { d: STROKE, fill: `url(#${id})` }))
}
