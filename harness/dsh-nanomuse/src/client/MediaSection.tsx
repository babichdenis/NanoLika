/**
 * Settings → Media: the model that draws the face's stills. Video generation is switched
 * off for now — the code lives in the source but the UI entry points are gone (0.1.x) —
 * so this page keeps the four controls that matter for pictures only:
 *   • the image model that draws the face (the account's first while signed in),
 *   • «Draw a new face» — the avatar studio, where a fresh look is drawn,
 *   • «Draw nothing» — pictures and clips off, one switch,
 *   • «Restore Lika's look» — drop a drawn look (a cat or whatever else was tried)
 *     and go back to the built-in Lika.
 *
 * With no face drawn yet the studio row is the way in: the person should not have to find
 * it elsewhere (the profile drawer, or asking the agent).
 */
import { createElement as h, useCallback, useEffect, useState, type ReactNode } from 'react'
import { call, errorStyle, failureText, type Translate } from './api.ts'
import { studioBus } from './AvatarStudio.tsx'
import { IconImage } from './icons.tsx'
import { useLive, type LiveMotion } from './live.ts'
import { SlotPicker, useModels } from './ModelsSection.tsx'
import { UnavailableLine, useProviders } from './OwnKey.tsx'

export interface MediaView {
  imageModel: string
  /** Pictures (C11): the account, an own row with image models, or nowhere (`reason` `no_image`). Absent on an older host. */
  image?: { source: 'cloud' | 'provider' | 'none'; label: string; model: string; reason: string }
  /** Unused by this build: video generation is off in the UI until it is revived. */
  video: { source: 'cloud' | 'provider' | 'none'; label: string; model: string; models: { id: string; name: string }[]; off: boolean; reason: string }
  animate: boolean
  /** «Nothing is drawn»: pictures and clips are off. */
  off?: boolean
  motion: LiveMotion
}

function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange(next: boolean): void; label: string; disabled?: boolean }): ReactNode {
  return h('button', { type: 'button', role: 'switch', className: 'nm-switch', 'aria-checked': checked, 'aria-label': label, disabled, onClick: () => onChange(!checked) })
}

export function makeMediaSection(t: Translate) {
  return function MediaSection(): ReactNode {
    const live = useLive()
    const providers = useProviders(t)
    const models = useModels(t)
    const [view, setView] = useState<MediaView | null>(null)
    const [busy, setBusy] = useState<'image' | 'off' | 'restore' | null>(null)
    const [error, setError] = useState('')
    const reload = useCallback(() => {
      void call<MediaView>('media')
        .then(setView)
        .catch((err: unknown) => setError(failureText(t, err)))
    }, [])
    useEffect(() => reload(), [reload])
    // the model list and the source follow the account and the own keys: re-read when sign-in, a key row or the face changes
    const ownCount = live.ownKeys?.count ?? 0
    useEffect(() => reload(), [live.cloud.signedIn, live.profile.faceId, ownCount, reload])

    const setImage = (provider: string, id: string) => {
      setBusy('image')
      setError('')
      void call('image-model', id === 'auto' ? { model: '' } : { model: id, provider })
        .then(() => { reload(); models.reload() })
        .catch((err: unknown) => setError(failureText(t, err)))
        .finally(() => setBusy(null))
    }
    const setOff = (off: boolean) => {
      setBusy('off')
      setError('')
      void call<MediaView>('media', { off })
        .then(setView)
        .catch((err: unknown) => setError(failureText(t, err)))
        .finally(() => setBusy(null))
    }
    /** Drop the drawn look and wear the built-in Lika again. */
    const restoreDefault = () => {
      if (!window.confirm(t('mdRestoreConfirm'))) return
      setBusy('restore')
      setError('')
      void call('profile', { avatar: 'dragon' })
        .then(() => reload())
        .catch((err: unknown) => setError(failureText(t, err)))
        .finally(() => setBusy(null))
    }
    /** The studio that draws her face, opened on the look she wears now. */
    const drawFace = () => studioBus.open?.(live.profile.description || '', live.profile.style || 'muse')

    if (!view) return h('div', { className: 'nm-section' }, h('p', null, t('mdMediaLead')), error ? h('div', { style: errorStyle }, error) : null)

    // the gate's sentences (C11): nothing configured has image models → who could, where the person is
    const gate = providers.view
    const noImage = gate && !gate.capabilities.includes('image') && !view.imageModel && view.image?.source !== 'provider'
    // the image row: the slot's source and model, as Settings → Models resolves them (`<provider> · <model>`)
    const imageSub = view.image && view.image.source !== 'none'
      ? `${t('mdImageModelSub')} · ${view.image.label}${view.image.model ? ` · ${view.image.model}` : ''}`
      : view.imageModel
        ? `${t('mdImageModelSub')} · NanoLika Cloud · ${view.imageModel}`
        : noImage
          ? h(UnavailableLine, { t, view: gate, capability: 'image', link: true, className: 'nm-wrap', tag: 'span' })
          : t('mdImageNone')
    const slots = models.view?.slots
    const imageSelect = slots && slots.image.options.length ? h(SlotPicker, { t, slot: 'image', view: slots.image, disabled: busy !== null, ...(gate ? { catalogue: gate.catalogue } : {}), onPick: setImage }) : null

    if (view.off) {
      return h(
        'div',
        { className: 'nm-section' },
        h('p', null, t('mdMediaLead')),
        h(
          'div',
          { className: 'nm-card' },
          h(
            'div',
            { className: 'nm-row' },
            h('div', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, t('mdOff')), h('span', { className: 'nm-row-sub nm-wrap' }, t('mdOffSub'))),
            h(Switch, { checked: true, label: t('mdOff'), disabled: busy !== null, onChange: setOff }),
          ),
          h('div', { className: 'nm-row' }, h('span', { className: 'nm-row-sub nm-wrap' }, t('mdOffLine'))),
        ),
        error ? h('div', { style: errorStyle }, error) : null,
      )
    }

    return h(
      'div',
      { className: 'nm-section' },
      h('p', null, t('mdMediaLead')),
      h(
        'div',
        { className: 'nm-card' },
        h(
          'div',
          { className: 'nm-row' },
          h('span', { className: 'nm-row-icon' }, h(IconImage, { size: 18 })),
          h('div', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, t('mdImageModel')), h('span', { className: 'nm-row-sub nm-wrap' }, imageSub)),
          imageSelect,
        ),
      ),
      h(
        'div',
        { className: 'nm-card' },
        h(
          'div',
          { className: 'nm-row' },
          h('div', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, t('mdDrawFace')), h('span', { className: 'nm-row-sub nm-wrap' }, t('mdDrawFaceSub'))),
          h('button', { type: 'button', className: 'nm-pill nm-pill-sm', disabled: busy !== null, onClick: drawFace }, t('mdDrawFace')),
        ),
      ),
      h(
        'div',
        { className: 'nm-card' },
        h(
          'div',
          { className: 'nm-row' },
          h('div', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, t('mdOff')), h('span', { className: 'nm-row-sub nm-wrap' }, t('mdOffSub'))),
          h(Switch, { checked: false, label: t('mdOff'), disabled: busy !== null, onChange: setOff }),
        ),
      ),
      h(
        'div',
        { className: 'nm-card' },
        h(
          'div',
          { className: 'nm-row' },
          h('div', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, t('mdRestore')), h('span', { className: 'nm-row-sub nm-wrap' }, t('mdRestoreSub'))),
          h('button', { type: 'button', className: 'nm-pill nm-pill-ghost nm-pill-sm', disabled: busy !== null, onClick: restoreDefault }, t('mdRestore')),
        ),
      ),
      error ? h('div', { style: errorStyle }, error) : null,
    )
  }
}