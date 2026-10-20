/**
 * The small spend meter at the top right of a chat, the way a status bar keeps
 * money in sight: what today cost in yuan and tokens, the pool's left under the
 * hover; a click opens the account. Occupies `conversation.session.header.utilities`,
 * next to Invite. Reads the relay's `GET /v1/me` through the host's pass-through
 * (`call('me')`, the same sheet the account page reads) once a minute — the relay
 * already answers the account page from memory, so the extra call is free there.
 * Nothing shows without an account.
 */
import { createElement as h, useEffect, useState, type ReactNode } from 'react'
import { call, type Translate } from './api.ts'
import { type AccountSheet } from './AccountPage.tsx'
import { useLive } from './live.ts'
import { settingsBus } from './bus.ts'

/** How often the figure is re-read; the relay re-reads the account after its own turns anyway. */
const REFRESH_MS = 60_000

const yuan = (n: number): string => `¥${n.toFixed(n % 1 === 0 ? 0 : 2)}`

/** `12834` → `12k`, `1_240_000` → `1.2M` — the token figures as a status bar prints them. */
const tokens = (n: number): string => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M` : n >= 1_000 ? `${Math.round(n / 1_000)}k` : `${n}`)

/** Today's cost and tokens from the sheet: every kind's row summed (chat, hands, images, …). */
function todayOf(sheet: AccountSheet | undefined): { cny: number; tokens: number } {
  const rows = sheet?.usage?.today?.by_kind ?? []
  let cny = 0
  let used = 0
  for (const row of rows) {
    cny += row.cost_cny ?? 0
    used += (row.prompt_tokens ?? 0) + (row.completion_tokens ?? 0)
  }
  return { cny, tokens: used }
}

export function makeUsagePill(t: Translate) {
  return function UsagePill(): ReactNode {
    const live = useLive()
    const [sheet, setSheet] = useState<AccountSheet | undefined>()
    useEffect(() => {
      if (!live.cloud.signedIn) return
      let alive = true
      const read = (): void => {
        void call<AccountSheet>('me')
          .then((next) => { if (alive) setSheet(next) })
          .catch(() => undefined)
      }
      read()
      const timer = setInterval(read, REFRESH_MS)
      return () => { alive = false; clearInterval(timer) }
    }, [live.cloud.signedIn])
    if (!live.cloud.signedIn) return null
    const spend = sheet?.spend
    const today = todayOf(sheet)
    // signed in but the sheet not read yet: one quiet dash, the pill stays out of the way
    if (!sheet) return h('span', { className: 'nm-usage-pill nm-usage-pill-empty', title: t('usageLoading') }, '—')
    const left = spend && !spend.unlimited ? spend.left ?? Math.max(0, (spend.grant ?? 0) - (spend.total ?? 0)) : null
    const title = spend?.unlimited
      ? t('usagePillUnlimited')
      : left === null
        ? t('usagePillTitle')
        : t('usagePillLeft', { left: yuan(left), grant: yuan(spend?.grant ?? 0) })
    const low = left !== null && left <= ((spend?.grant ?? 0) * 0.2)
    return h(
      'button',
      {
        type: 'button',
        className: `nm-usage-pill${low ? ' nm-usage-pill-low' : ''}`,
        title,
        onClick: () => settingsBus.openSection?.('nanomuse-cloud'),
      },
      h('span', { className: 'nm-usage-pill-money' }, yuan(today.cny)),
      today.tokens > 0 ? h('span', { className: 'nm-usage-pill-tokens' }, t('usagePillTokens', { n: tokens(today.tokens) })) : null,
    )
  }
}
