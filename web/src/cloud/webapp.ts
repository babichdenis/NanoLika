/**
 * The mini-app doors: Telegram and MAX hand the page a signed `initData`, and the relay
 * turns it into the same account key every other client uses.
 *
 * The app never trusts the payload itself: it forwards it to `/v1/auth/telegram-webapp` or
 * `/v1/auth/max-webapp`, which validates the signature with the bot token and answers with
 * an account key. One account, whichever door was used.
 */
import { isRecord, relayBase, setCloudToken } from "./client";

export type Surface = "telegram" | "max" | "browser";

declare global {
  interface Window {
    Telegram?: { WebApp?: { initData?: string; themeParams?: Record<string, string>; ready?: () => void; expand?: () => void } };
    WebApp?: { initData?: string };
  }
}

/** Which messenger opened this page, if any. */
export function surface(): Surface {
  if (typeof window === "undefined") return "browser";
  if (window.Telegram?.WebApp?.initData) return "telegram";
  if (window.WebApp?.initData) return "max";
  if (hashData()) return "telegram";
  return "browser";
}

/** `#tgWebAppData=…` is how Telegram hands the payload over before its SDK is ready. */
function hashData(): string {
  const params = new URLSearchParams(location.hash.slice(1));
  return params.get("tgWebAppData") || params.get("WebAppData") || "";
}

function launchData(which: Surface): string {
  if (which === "telegram") return window.Telegram?.WebApp?.initData || hashData();
  if (which === "max") return window.WebApp?.initData || hashData();
  return "";
}

/** Tell the messenger the page is ready and take the full height it offers. */
export function announce(): void {
  try {
    window.Telegram?.WebApp?.ready?.();
    window.Telegram?.WebApp?.expand?.();
  } catch {
    /* an older client without these calls is not a failure */
  }
}

/**
 * Sign in through the messenger. Returns the account key, or `null` when the page was not
 * opened from a messenger or the messenger did not hand over a payload.
 */
export async function signInFromWebApp(): Promise<string | null> {
  const which = surface();
  if (which === "browser") return null;
  const initData = launchData(which);
  if (!initData) return null;
  const path = which === "telegram" ? "/v1/auth/telegram-webapp" : "/v1/auth/max-webapp";
  const response = await fetch(relayBase() + path, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ init_data: initData }),
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = isRecord(data) && isRecord(data.error) ? data.error : {};
    throw new Error(String(error.message || "Мессенджер не подтвердил вход."));
  }
  const key = isRecord(data) ? String(data.api_key || "") : "";
  if (key) setCloudToken(key);
  return key || null;
}
