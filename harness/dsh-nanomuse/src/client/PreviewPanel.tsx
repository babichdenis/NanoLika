/**
 * The preview pane: a page or an artifact the agent put beside the chat, with a
 * small head (title, open-in-a-tab, close) and the content itself — an iframe
 * for a URL or inline HTML, an image, or a PDF. Read from `preview.ts`; the
 * pane itself is the harness's `main` seat shown through `win.split`.
 *
 * While it is on screen the page carries `data-nm-preview-pane`, which gives it a
 * narrower width than a room and, on a small window, the whole screen — the
 * conversation behind it is never squeezed (styles.ts).
 */
import { createElement as h, useEffect, type ReactNode } from 'react'
import type { Translate } from './api.ts'
import { hidePreview, usePreview } from './preview.ts'

export function makePreviewPanel(t: Translate) {
  return function PreviewPanel(): ReactNode {
    const p = usePreview()
    useEffect(() => {
      document.documentElement.dataset['nmPreviewPane'] = ''
      return () => { delete document.documentElement.dataset['nmPreviewPane'] }
    }, [])
    if (!p) return h('div', { className: 'nm-preview' }, h('div', { className: 'nm-preview-empty' }, t('previewEmpty')))
    const title = p.title || p.url || p.kind
    return h(
      'div',
      { className: 'nm-preview' },
      h(
        'div',
        { className: 'nm-preview-head' },
        h('span', { className: 'nm-preview-title', title }, title),
        p.url
          ? h('a', { className: 'nm-preview-open', href: p.url, target: '_blank', rel: 'noreferrer' }, t('previewOpen'))
          : null,
        h('button', { className: 'nm-preview-close', type: 'button', onClick: hidePreview, title: t('previewClose'), 'aria-label': t('previewClose') }, '×'),
      ),
      h(
        'div',
        { className: 'nm-preview-body' },
        p.kind === 'html'
          ? h('iframe', { srcDoc: p.html, sandbox: 'allow-scripts allow-same-origin', title })
          : p.kind === 'image'
            ? h('img', { src: p.src ?? p.url, alt: title })
            : p.kind === 'pdf'
              ? h('embed', { src: p.url, type: 'application/pdf', title })
              : h('iframe', { src: p.url, title, referrerPolicy: 'no-referrer' }),
      ),
    )
  }
}
