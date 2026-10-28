export const DEFAULT_TOPUP_URL: string;
export interface FundsInfo {
  code: "insufficient_funds";
  required_rub?: number;
  balance_rub?: number;
  currency?: string;
  topup_url?: string;
}
export function safeTopupUrl(value: unknown): string;
export function fundsView(error: unknown, language?: string): {
  message: string;
  topupLabel: string;
  cheaperLabel: string;
  topupUrl: string;
  requiredRub: number | null;
  balanceRub: number | null;
} | null;
