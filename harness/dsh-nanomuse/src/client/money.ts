/**
 * Money the way the relay prices it (0.27): rubles when the operator prices dollars in
 * them (RUB_PER_USD — his own price, not the market's — plus the top-up fee), yuan until
 * then. Every figure the apps print goes through here, so a number never carries another
 * currency's sign: ¥ → $ at the relay's USD/CNY rate, $ → ₽ at the operator's rate, plus
 * the fee. The rates ride on `/v1/config`, fetched once per window; an older relay sends
 * none of it and the apps keep showing yuan, as they always did. Figures the relay
 * pre-computes in rubles (`*_rub` on `/v1/me`) are printed as they come — this module is
 * for the figures that only exist in yuan: quotes, refusals, the sign-in pages.
 */
import { useEffect, useState } from 'react'
import { call } from './api.ts'

/** The three figures of `/v1/config` money is shown by; the rest of the config is not needed here. */
export interface MoneyRates { usd_cny?: number | undefined; rub_per_usd?: number | undefined; topup_fee_pct?: number | undefined }

let rates: MoneyRates | undefined
let pending: Promise<MoneyRates> | undefined

/** Whether this relay prices in rubles at all (`RUB_PER_USD = 0` keeps it on yuan). */
export function rubOn(r?: MoneyRates | null): boolean {
  return (r?.rub_per_usd ?? 0) > 0 && (r?.usd_cny ?? 0) > 0
}

/** The operator's rubles for a yuan of spend; 0 when rubles are off. */
export function toRub(cny: number, r?: MoneyRates | null): number {
  const usdCny = r?.usd_cny ?? 0
  const perUsd = r?.rub_per_usd ?? 0
  if (perUsd <= 0 || usdCny <= 0) return 0
  return (cny / usdCny) * perUsd * (1 + (r?.topup_fee_pct ?? 0) / 100)
}

/** One figure as the apps print it: `₽27` when the relay prices in rubles, `¥1.94` until then. */
export function cash(cny: number | null | undefined, r?: MoneyRates | null): string {
  if (cny === null || cny === undefined) return '—'
  const rub = rubOn(r)
  const n = rub ? toRub(cny, r) : cny
  return `${rub ? '₽' : '¥'}${n.toFixed(n % 1 === 0 ? 0 : 2)}`
}

/** The rates for a render, fetched once per window — the same route `useCloudConfig` reads. */
export function useMoney(): { rub: boolean; rates: MoneyRates; cash: (cny: number | null | undefined) => string } {
  const [r, setRates] = useState<MoneyRates>(rates ?? {})
  useEffect(() => {
    if (rates) return
    const p: Promise<MoneyRates> = (pending ??= call<MoneyRates>('config')
      .then((c): MoneyRates => {
        const next: MoneyRates = { usd_cny: c?.usd_cny, rub_per_usd: c?.rub_per_usd, topup_fee_pct: c?.topup_fee_pct }
        rates = next
        return next
      })
      .catch((): MoneyRates => { rates = {}; return {} }))
    let alive = true
    void p.then((x) => { if (alive) setRates(x) })
    return () => { alive = false }
  }, [])
  return { rub: rubOn(r), rates: r, cash: (cny) => cash(cny, r) }
}
