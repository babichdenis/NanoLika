/**
 * What a figure of the relay's yuan is printed as.
 *
 * The relay bills in yuan (its provider's currency) and answers every figure twice: in yuan
 * and, when the operator prices in rubles, in rubles (`rub_per_usd` is the operator's own
 * price of a dollar, `topup_fee_pct` the commission on a top-up; both ride on top of
 * `usd_cny`). A person should never have to read a currency their operator does not charge
 * in, so every screen asks this module for the symbol, the converted figure and the
 * translated line with its `¥` already replaced.
 */
import { useCloudConfig } from "./components/StarNudge";

/** The rates a figure needs; any of the three payloads that carry them will do. */
export interface Rates {
  rub_per_usd?: number;
  topup_fee_pct?: number;
  usd_cny?: number;
}

export interface Money {
  /** "₽" or "¥" */
  readonly symbol: string;
  readonly rub: boolean;
  /** the figure in the currency a person reads */
  value(cny: number): number;
  /** "₽50.00" */
  text(cny: number, digits?: number): string;
  /** a translated line: its ¥ becomes the symbol ("+¥5 for each friend" → "+₽50") */
  s(text: string): string;
}

export function money(rates: Rates | undefined | null): Money {
  const perUsd = rates?.rub_per_usd ?? 0;
  const usdCny = rates?.usd_cny ?? 0;
  const fee = rates?.topup_fee_pct ?? 0;
  const rub = perUsd > 0 && usdCny > 0;
  // ¥ → $ at the relay's rate, $ → ₽ at the operator's, then his commission on top
  const k = rub ? (perUsd / usdCny) * (1 + fee / 100) : 1;
  const symbol = rub ? "₽" : "¥";
  return {
    symbol,
    rub,
    value: (cny) => cny * k,
    text: (cny, digits = 2) => `${symbol}${(cny * k).toFixed(digits)}`,
    s: (text) => (rub ? text.replace(/¥/g, symbol) : text),
  };
}

/** The money of the relay this app is signed in to. The config is already fetched once for
 * the whole app (the sign-in page prints its figures too), so this costs nothing. */
export function useMoney(): Money {
  return money(useCloudConfig());
}
