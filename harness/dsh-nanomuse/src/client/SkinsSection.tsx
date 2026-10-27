/**
 * Settings → Skins: the themes for the app's own look, from the person's skins collection on
 * their site (`skins/index.json`, kind "theme"). The list is fetched through the host with
 * refresh=1 every time it opens, so a theme added or edited in the folder shows up without
 * reinstalling the app; "Muse" is the built-in default and always works offline. The choice is
 * kept in localStorage and applied by the stylesheet's theme layer (`styles.ts`).
 */
import { createElement as h, useCallback, useEffect, useState, type ReactNode } from 'react'
import { call, errorStyle, failureText, type Skin, type Translate } from './api.ts'
import { applyTheme, themeCss } from './styles.ts'

const THEME_KEY = 'nanomuse.theme'

function readChoice(): string {
  try {
    return localStorage.getItem(THEME_KEY) ?? ''
  } catch {
    return ''
  }
}

function remember(id: string): void {
  try {
    if (id) localStorage.setItem(THEME_KEY, id)
    else localStorage.removeItem(THEME_KEY)
  } catch {
    /* private mode: the theme still applies for this session */
  }
}

/** A theme row's preview: the light and dark base colours as two dots. */
function Swatches({ theme }: { theme: Skin }): ReactNode {
  const light = theme.light?.['--nm-base'] ?? 'transparent'
  const dark = theme.dark?.['--nm-base'] ?? 'transparent'
  const accent = theme.light?.['--nm-accent'] ?? 'transparent'
  return h('span', { className: 'nm-skin-swatches' },
    h('span', { className: 'nm-skin-dot', style: { background: light, border: '1px solid rgba(0,0,0,0.15)' } }),
    h('span', { className: 'nm-skin-dot', style: { background: dark } }),
    h('span', { className: 'nm-skin-dot', style: { background: accent } }),
  )
}

export function makeSkinsSection(t: Translate) {
  return function SkinsSection(): ReactNode {
    const [themes, setThemes] = useState<Skin[]>([])
    const [chosen, setChosen] = useState<string>(readChoice)
    const [error, setError] = useState('')
    const [busy, setBusy] = useState(false)

    const load = useCallback(() => {
      setBusy(true)
      setError('')
      call<{ skins: Skin[] }>('skins/list?refresh=1')
        .then((d) => setThemes((d.skins ?? []).filter((s) => Boolean(s) && s.kind === 'theme' && s.light && s.dark)))
        .catch((err: unknown) => setError(failureText(t, err)))
        .finally(() => setBusy(false))
    }, [t])

    useEffect(() => { load() }, [load])

    const choose = (id: string) => {
      if (!id) {
        applyTheme(undefined)
        remember('')
        setChosen('')
        return
      }
      const theme = themes.find((s) => s.id === id)
      if (!theme || !theme.light || !theme.dark) return
      applyTheme(themeCss(theme))
      remember(id)
      setChosen(id)
    }

    const row = (id: string, name: string, sub: string, extra?: ReactNode) =>
      h('button', {
        key: id,
        type: 'button',
        className: `nm-card nm-skin-row${chosen === id ? ' nm-skin-row-active' : ''}`,
        'aria-pressed': chosen === id,
        disabled: busy,
        onClick: () => choose(id),
      },
        h('span', { className: 'nm-row-main' }, h('span', { className: 'nm-row-title' }, name), h('span', { className: 'nm-row-sub nm-wrap' }, sub)),
        extra ?? null,
      )

    return h(
      'div',
      { className: 'nm-section' },
      h('p', null, t('skLead')),
      h(
        'div',
        { className: 'nm-row' },
        h('span', { style: { flex: 1 } }),
        h('button', { type: 'button', className: 'nm-pill nm-pill-ghost nm-pill-sm', disabled: busy, onClick: load }, t('skRefresh')),
      ),
      error ? h('div', { style: errorStyle }, error) : null,
      row('', t('skDefault'), t('skDefaultSub')),
      themes.length
        ? themes.map((s) => row(s.id, s.name, t('skThemeSub'), h(Swatches, { theme: s })))
        : busy
          ? h('p', { className: 'nm-fine' }, t('skLoading'))
          : h('p', { className: 'nm-fine' }, t('skEmpty')),
      h('p', { className: 'nm-fine nm-wrap' }, t('skSite')),
    )
  }
}
