/**
 * Where the main window may go. It shows the Host's web app and nothing else from the
 * network: a navigation stays inside the Host's origin, or is one of the shell's own pages
 * from disk (the loading page); anything else is opened in the person's browser when it is
 * http(s), and dropped otherwise. Pure functions, so the tests can drive them without Electron.
 */

/** The only links that leave the app: http(s) with a host. */
export const EXTERNAL_URL = /^https?:\/\/[^/]/;

/**
 * Whether `url` is on `hostUrl`'s origin (scheme, host and port), compared as parsed URLs:
 * a prefix test let `http://127.0.0.1:38421@evil.example/` through, the Host's origin
 * standing as the user-info part of another host's address.
 */
export function sameOrigin(url: string, hostUrl: string | null): boolean {
  if (!hostUrl) return false;
  try {
    return new URL(url).origin === new URL(hostUrl).origin;
  } catch {
    return false;
  }
}

/** Whether `url` is one of the shell's own pages: a `file:` URL under `ownResources` (a directory path). */
export function ownPage(url: string, ownResourcesHref: string): boolean {
  if (!url.startsWith("file:")) return false;
  const base = ownResourcesHref.endsWith("/") ? ownResourcesHref : `${ownResourcesHref}/`;
  return url.startsWith(base);
}

export type NavigationVerdict = "allow" | "external" | "deny";

/** What `will-navigate` does with `url`: let it through, hand it to the browser, or drop it. */
export function navigationVerdict(url: string, hostUrl: string | null, ownResourcesHref: string): NavigationVerdict {
  if (sameOrigin(url, hostUrl) || ownPage(url, ownResourcesHref)) return "allow";
  return EXTERNAL_URL.test(url) ? "external" : "deny";
}

/** The sentence the Host answers a request that carries no token and no cookie: a plain
 *  reload of the page, a route opened without the URL `dsh web` printed, a stale cookie. The
 *  window is then a document with no widget at all, and nothing can be typed into it. */
export const LOST_TOKEN = "dsh web authentication required";

/** How long after one repair another is refused: a Host that keeps refusing a good token must
 *  not set the window spinning. */
export const HEAL_INTERVAL_MS = 10_000;

/**
 * Whether a finished page needs the tokenised Host URL loaded into it again. It has to be on
 * the Host's origin (the shell's own loading page is a `file:` URL and is left alone), and
 * either show the Host's own 401 sentence or show nothing at all, which is what a document
 * that never loaded leaves behind.
 */
export function needsHostUrl(here: string, hostUrl: string | null, text: string): boolean {
  if (!sameOrigin(here, hostUrl)) return false;
  const shown = text.trim();
  return shown === "" || shown.startsWith(LOST_TOKEN);
}

/** Whether a repair may run now, given when the last one did. `now - last >= HEAL_INTERVAL_MS`. */
export function mayHeal(last: number, now: number): boolean {
  return now - last >= HEAL_INTERVAL_MS;
}
