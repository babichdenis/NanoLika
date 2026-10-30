/**
 * The model pill of the chat header: what answers now, and the relay-hosted catalogue
 * behind it. The picker's markup and styles are served by the relay (`/web/picker/`),
 * so the shell only opens a window and takes the answer — a change there reaches every
 * installed app without a new bundle.
 */
import { createElement as h, useState, type ReactNode } from 'react'
import { call, type Translate } from './api.ts'
import { bridge } from './bridge.ts'
import { settingsBus } from './bus.ts'
import { MODELS_SECTION, useModels } from './ModelsSection.tsx'

interface PickerSession {
  url: string
  key: string
  expiresAt?: number
}

export function makeCloudModelPill(t: Translate) {
  return function CloudModelPill(): ReactNode {
    const { view, reload } = useModels(t)
    const [busy, setBusy] = useState(false)
    const picker = bridge()?.modelPicker
    if (!picker || !view?.signedIn || !view.cloudModels) return null
    const slot = view.slots.chat
    if (slot.provider !== 'nanomuse' || !slot.model) return null
    const name = slot.options.find((option) => option.id === slot.model)?.name ?? slot.model
    const open = async (anchor: { x: number; y: number; width: number; height: number }): Promise<void> => {
      setBusy(true)
      try {
        const session = await call<PickerSession>('model-picker-session', {})
        const answer = await picker.open({ url: session.url, key: session.key, selected: slot.model, kind: 'chat', title: t('mlTitle'), anchor })
        if (answer?.type === 'pick' && answer.id && answer.id !== slot.model) {
          await call('chat-model', { provider: 'nanomuse', model: answer.id })
          reload()
        } else if (answer?.type === 'manage') {
          settingsBus.openSection?.(MODELS_SECTION)
        }
      } catch {
        // the pill stays as it was; the next click asks the host again
      } finally {
        setBusy(false)
      }
    }
    return h(
      'button',
      {
        type: 'button',
        className: 'nm-pill nm-pill-ghost nm-pill-sm nm-model-pill',
        'data-testid': 'nm-model-pill',
        disabled: busy,
        title: `${t('mlCloudModels')}: ${name}`,
        'aria-label': `${t('mlCloudModels')}: ${name}`,
        onClick: (event) => { const r = event.currentTarget.getBoundingClientRect(); void open({ x: r.x, y: r.y, width: r.width, height: r.height }); },
      },
      `${t('mlTitle')} · ${name}`,
    )
  }
}
