/**
 * One priced refusal, in the client's own words.
 *
 * This is the runtime client's copy: the cloud browser carries the same view in plain
 * JavaScript (`cloud/nanomuse_cloud/web_client/funds.js`) and the two are kept in step until
 * that client is archived. The client must not import from `cloud/` — that boundary is what
 * keeps server code out of the bundle.
 */

export interface FundsInfo {
  message: string;
  topupLabel: string;
  cheaperLabel: string;
  topupUrl: string;
  requiredRub: number | null;
  balanceRub: number | null;
}

const words: Record<string, Record<"known" | "unknown" | "topup" | "cheaper", string>> = {
  ru: {
    known: "Нужно {required}, на балансе {balance}. Пополните баланс или выберите модель дешевле.",
    unknown: "Для этой генерации недостаточно средств. Пополните баланс или выберите модель дешевле.",
    topup: "Пополнить",
    cheaper: "Выбрать модель дешевле",
  },
  en: {
    known: "This generation needs {required}; your balance is {balance}. Top up or choose a cheaper model.",
    unknown: "This generation costs more than your balance. Top up or choose a cheaper model.",
    topup: "Top up",
    cheaper: "Choose a cheaper model",
  },
  zh: {
    known: "本次生成需要 {required}，余额为 {balance}。请充值或选择更便宜的模型。",
    unknown: "余额不足以完成本次生成。请充值或选择更便宜的模型。",
    topup: "充值",
    cheaper: "选择更便宜的模型",
  },
};

export const DEFAULT_TOPUP_URL = "https://cloud.nanolika.ru/app";

export function safeTopupUrl(value: unknown): string {
  if (typeof value !== "string" || !value.trim() || value.trim().startsWith("//")) return DEFAULT_TOPUP_URL;
  try {
    const url = new URL(value, DEFAULT_TOPUP_URL);
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (url.username || url.password || (url.protocol !== "https:" && !(url.protocol === "http:" && local))) {
      return DEFAULT_TOPUP_URL;
    }
    return url.href;
  } catch {
    return DEFAULT_TOPUP_URL;
  }
}

export function fundsView(error: any, language = "en"): FundsInfo | null {
  const data = error?.funds ?? error?.details ?? error?.error ?? error;
  if (data?.code !== "insufficient_funds") return null;
  const lang = String(language).toLowerCase().split("-")[0];
  const copy = words[lang] ?? words.en;
  const amount = (value: unknown): number | null =>
    typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
  const required = amount(data.required_rub);
  const balance = amount(data.balance_rub);
  const known = required !== null && balance !== null && (!data.currency || data.currency === "RUB");
  const rub = (value: number) =>
    "₽" +
    new Intl.NumberFormat(lang === "zh" ? "zh-CN" : lang === "ru" ? "ru-RU" : "en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  return {
    message: known
      ? copy.known.replace("{required}", rub(required)).replace("{balance}", rub(balance))
      : copy.unknown,
    topupLabel: copy.topup,
    cheaperLabel: copy.cheaper,
    topupUrl: safeTopupUrl(data.topup_url),
    requiredRub: required,
    balanceRub: balance,
  };
}
