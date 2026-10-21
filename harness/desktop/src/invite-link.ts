/**
 * A share link's code, as the site hands it over: `nanolika://invite/ABCD1234`, or the same
 * with `?code=`/`?invite=` (docs/cloud.md's invite, relay 0.9). The link comes from
 * nanolika.ru, which shows the code from `?invite=`; opening it here saves the person
 * typing eight characters on a phone.
 *
 * Pure on purpose (no Electron): the parsing rules are the ones tests/invite-link.test.mjs
 * holds, and a link that is not one of ours is dropped rather than guessed at.
 */

/** The scheme the site's "Открыть в Лике" button uses; also in electron-builder.yml's `protocols`. */
export const INVITE_SCHEME = "nanolika";

/** The relay's codes are eight of `ABCDEFGHJKMNPQRSTUVWXYZ23456789`; the app uppercases as it types. */
const CODE = /^[A-Za-z0-9-]{4,32}$/;

/** The words a link may put where the code goes: they say what the link is for, they are not a code. */
const WORDS = new Set(["invite", "invites", "code", "signin", "sign-in", "auth", "account"]);

/** The code in one candidate string, upper-cased; "" when the string carries none. */
export function inviteCode(raw: string): string {
  const value = raw.trim().replace(/^#+/, "");
  if (!CODE.test(value)) return "";
  return value.toUpperCase();
}

/**
 * The code in one command-line argument or `open-url` address: `nanolika://invite/CODE`,
 * with the code as the path, as `?code=`, as `?invite=`, or as the host of a bare
 * `nanolika:CODE`. Anything else, another scheme, another host: "".
 */
export function inviteFromUrl(raw: string): string {
  const text = (raw || "").trim();
  if (!text.toLowerCase().startsWith(INVITE_SCHEME + ":")) return "";
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    // nanolika:ABCD1234 — no slashes, which URL reads as a scheme and an opaque path
    const rest = text.slice(INVITE_SCHEME.length + 1).replace(/^\/+/, "");
    const head = rest.split(/[?#]/)[0] ?? "";
    const part = head.split("/").filter(Boolean).pop() ?? "";
    return inviteCode(part);
  }
  const query = url.searchParams.get("code") || url.searchParams.get("invite") || "";
  if (query) return inviteCode(query);
  const parts = url.pathname.split("/").filter(Boolean);
  const last = parts[parts.length - 1] ?? "";
  if (last) return inviteCode(last);
  // nanolika://ABCD2345 puts the code where the host goes; nanolika://invite puts a word there
  const host = url.hostname || "";
  return host && !WORDS.has(host.toLowerCase()) ? inviteCode(host) : "";
}

/** The first invite link among command-line arguments (Windows and Linux hand it over this way). */
export function inviteFromArgv(argv: readonly string[]): string {
  for (const arg of argv) {
    const code = inviteFromUrl(arg);
    if (code) return code;
  }
  return "";
}
