/**
 * The preview pane: a page or an artifact the agent put beside the chat, with a
 * small head (title, open-in-a-tab, close) and the content itself — an iframe
 * for a URL or inline HTML, an image, or a PDF. Read from `preview.ts`; the
 * pane itself is the harness's `main` seat shown through `win.split`.
 */
import { createElement as h, type ReactNode } from 'react'
import type { Translate } from './api.ts'
import { hidePreview, usePreview } from './preview.ts'

export function makePreviewPanel(t: Translate) {
  return function PreviewPanel(): ReactNode {
    const p = usePreview()
    if (!p) return h('div', { className: 'nm-preview' }, h('div', { className: 'nm-preview-empty' }, t('previewEmpty')))
    const title = p.title || p.url || p.kind
    return h(
      'div',
      { className: 'nm-preview' },
      h(
        'div',
        { className: 'nm-preview-head' },
        h('span', { className: 'nm-preview-title' }, title),
        p.url
          ? h('a', { className: 'nm-preview-open', href: p.url, target: '_blank', rel: 'noreferrer' }, t('previewOpen'))
          : null,
        h('button', { className: 'nm-preview-close', onClick: hidePreview }, t('previewClose')),
      ),
      h(
        'div',
        { className: 'nm-preview-body' },
        p.kind === 'html'
          ? h('iframe', { srcDoc: p.html, sandbox: 'allow-scripts allow-same-origin', title })
          : p.kind === 'image'
            ? h('img', { src: p.src, alt: title })
            : p.kind === 'pdf'
              ? h('embed', { src: p.url, type: 'application/pdf', title })
              : h('iframe', { src: p.url, title }),
      ),
    )
  }
}
