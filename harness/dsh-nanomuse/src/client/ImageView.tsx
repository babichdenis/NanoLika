/**
 * A picture the chat shows, opened to the window's own size: a click on any picture in
 * a conversation (a messenger's, the relay's, the web's) lands here, with a way to keep
 * it. Sits in the `shell.overlay` seat, so it covers the frame and closes with Escape or
 * a click on the dark around it.
 */
import { createElement as h, useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { Translate } from './api.ts'
import { hideImage, useImageView } from './image-view.ts'
import { saveMedia } from './media.ts'

export function makeImageView(t: Translate) {
  return function ImageView(): ReactNode {
    const shown = useImageView()
    useEffect(() => {
      if (!shown) return
      const onKey = (event: KeyboardEvent): void => { if (event.key === 'Escape') hideImage() }
      document.addEventListener('keydown', onKey)
      document.body.classList.add('nm-image-open')
      return () => {
        document.removeEventListener('keydown', onKey)
        document.body.classList.remove('nm-image-open')
      }
    }, [shown])
    if (!shown) return null
    return createPortal(
      h('div', {
        className: 'nm-image-view',
        role: 'dialog',
        'aria-modal': 'true',
        'aria-label': shown.name || '',
        onClick: (event: { target: EventTarget | null; currentTarget: EventTarget | null }) => {
          if (event.target === event.currentTarget) hideImage()
        },
      },
      h('img', { className: 'nm-image-view-pic', src: shown.url, alt: shown.name || '' }),
      h('div', { className: 'nm-image-view-bar' },
        h('button', { type: 'button', className: 'nm-image-view-btn', onClick: () => { void saveMedia(shown.url, shown.name) } }, t('previewDownload')),
        h('button', { type: 'button', className: 'nm-image-view-btn', onClick: hideImage }, t('previewClose')))),
      document.body,
    )
  }
}
