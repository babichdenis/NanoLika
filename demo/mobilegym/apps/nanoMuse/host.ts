import { NANOMUSE_CONFIG } from './data';
import { endDemoSession, setReplyLanguage, type ReplyLanguage } from './demo';
import { useNanoMuseStore, type DemoRecord, type LinkState } from './state';

/**
 * The phone as a guest of a page.
 *
 * The showcase site puts the simulator in a frame and wraps a page around it — what the Muse
 * can do here, a few things to try, how long the session has. Same origin, so that page talks
 * to this app through `window.__NANOMUSE__` on the frame's window (the way MobileGym itself
 * exposes `__OS__` and `__SIM_INPUT__`) and hears from it through `subscribe`:
 *
 *   open()           bring the NanoLika app to the front
 *   draft(text)      open it and put `text` in the chat's composer — sending is the person's tap
 *   start()          what the Start pill does: ask the showcase for a Muse and open the chat;
 *                    false when the page is not at that step (no sign-in yet, a Muse already)
 *   reset()          end the hosted session and start a fresh Muse
 *   language(code)   'en' or 'zh': the page's language; a hosted Muse answers in it (its
 *                    reply language is set, now and for the sessions that follow)
 *   state()          where things stand: configured, link, the hosted session, the web app, the sign-in,
 *                    whether NanoLika is the app on the screen, whether start() would do anything
 *   subscribe(fn)    fn(state) now and on every change; returns the unsubscribe
 *
 * The web app inside the phone is on another origin (the session's hostname); the draft goes
 * to it with postMessage, which it accepts from its parent window only, and it says
 * `nanomuse:ready` when it has a composer to put the text in — a draft handed over before
 * that waits.
 */

export type WebState = 'closed' | 'loading' | 'ready';

export interface HostState {
  /** A server is configured (hosted or your own). */
  configured: boolean;
  link: LinkState;
  /** The hosted showcase session, when this phone is on one. */
  demo: DemoRecord | null;
  /** The web app in the frame. */
  web: WebState;
  /** The agent's name, as the web app reported it ("" until then). */
  name: string;
  /** Whether a hosted showcase is available at all (set at build time). */
  hosted: boolean;
  /** The visitor is signed in to NanoLika Cloud for the showcase (holds a ticket). */
  signedIn: boolean;
  /** NanoLika is the app on the screen right now (the page's dock lights its icon). */
  front: boolean;
  /** The welcome or meet page is up with its Start pill: `start()` would press it. */
  startable: boolean;
}

/** What the Start pill does, registered by the page that shows it while it is shown. */
type Starter = () => Promise<boolean>;
let starter: Starter | null = null;

/** SetupPage registers its Start while the pill is on the screen (null when it goes). */
export function setStarter(fn: Starter | null) {
  if (starter === fn) return;
  starter = fn;
  notify();
}

type OSWindow = Window & {
  __OS__?: {
    launchApp?: (id: string) => void;
    getState?: () => { activeAppId?: string | null };
    locale?: { getLocale?: () => string };
  };
};

function os() {
  return (window as OSWindow).__OS__;
}

type Listener = (state: HostState) => void;

const listeners = new Set<Listener>();
let frame: HTMLIFrameElement | null = null;
let web: WebState = 'closed';
let name = '';
let queued: string | null = null;

function current(): HostState {
  const s = useNanoMuseStore.getState();
  return {
    configured: !!s.serverUrl,
    link: s.link,
    demo: s.demo,
    web,
    name,
    hosted: !!NANOMUSE_CONFIG.demoGateway,
    signedIn: !!s.ticket,
    front: os()?.getState?.()?.activeAppId === NANOMUSE_CONFIG.appId,
    startable: starter !== null && !s.serverUrl,
  };
}

function notify() {
  const state = current();
  for (const fn of listeners) {
    try {
      fn(state);
    } catch (err) {
      console.error('[NanoLika host] listener failed', err);
    }
  }
}

function webOrigin(): string {
  try {
    return new URL(useNanoMuseStore.getState().serverUrl).origin;
  } catch {
    return '*';
  }
}

function post(text: string) {
  frame?.contentWindow?.postMessage({ type: 'nanomuse:draft', text }, webOrigin());
}

/** MusePage mounts and unmounts the web app's frame here. */
export function attachFrame(el: HTMLIFrameElement | null) {
  frame = el;
  web = el ? 'loading' : 'closed';
  if (!el) name = '';
  notify();
}

function openApp() {
  os()?.launchApp?.(NANOMUSE_CONFIG.appId);
}

function draft(text: string) {
  const clean = String(text ?? '').slice(0, 4000);
  if (!clean) return;
  openApp();
  if (web === 'ready') post(clean);
  else queued = clean;
}

async function reset() {
  const s = useNanoMuseStore.getState();
  if (s.demo && NANOMUSE_CONFIG.demoGateway) {
    await endDemoSession(NANOMUSE_CONFIG.demoGateway, s.demo.id, s.token);
  }
  queued = null;
  s.disconnect();
  openApp();
}

/**
 * The Start pill, pressed by the page around the phone (it turns the phone on and brings
 * NanoLika up without a tap, and starts the Muse once it has reason to think a person is
 * looking). Only the pill's own step: a welcome page that asks for a sign-in first, or a
 * Muse already running, leaves this a no-op.
 */
async function start(): Promise<boolean> {
  if (!starter || useNanoMuseStore.getState().serverUrl) return false;
  openApp();
  return starter();
}

/** The page's language, when it said; a hosted session takes it as its reply language. */
let wanted: ReplyLanguage | null = null;

function replyLanguageFor(code: string): ReplyLanguage {
  return /^zh/i.test(code) ? '中文' : 'English';
}

/**
 * A hosted Muse speaks the page's language, whatever it has just read on the screen: the
 * runtime's reply language is set from the page's choice, else from the phone's locale. Only
 * the showcase's own sessions; a server of the person's keeps its setting.
 */
function applyLanguage() {
  const s = useNanoMuseStore.getState();
  if (!s.demo || !s.serverUrl || !s.token) return;
  const language = wanted ?? replyLanguageFor(String(os()?.locale?.getLocale?.() ?? ''));
  void setReplyLanguage(s.serverUrl, s.token, language);
}

function language(code: string) {
  wanted = replyLanguageFor(code);
  applyLanguage();
}

function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  try {
    fn(current());
  } catch (err) {
    console.error('[NanoLika host] listener failed', err);
  }
  return () => {
    listeners.delete(fn);
  };
}

export const host = { open: openApp, draft, start, reset, language, state: current, subscribe };

/**
 * Called once by `state.ts` after the store exists (the two modules import each other, so
 * nothing here may touch the store while modules are still being evaluated).
 */
export function installHost() {
  window.addEventListener('message', (ev: MessageEvent) => {
    if (!frame || ev.source !== frame.contentWindow) return;
    const data = ev.data as { type?: unknown; name?: unknown; theme?: unknown } | null;
    if (!data || (data.type !== 'nanomuse:ready' && data.type !== 'nanomuse:theme')) return;
    // the web app's colour scheme, so the strips around its frame match it (it cannot be seen from outside)
    if (data.theme === 'light' || data.theme === 'dark') useNanoMuseStore.getState().setWebTheme(data.theme);
    if (data.type !== 'nanomuse:ready') return;
    web = 'ready';
    name = typeof data.name === 'string' ? data.name : '';
    if (queued) {
      post(queued);
      queued = null;
    }
    notify();
  });
  useNanoMuseStore.subscribe((s, prev) => {
    if (s.serverUrl !== prev.serverUrl || s.link !== prev.link || s.demo !== prev.demo || s.ticket !== prev.ticket)
      notify();
    // a hosted Muse has just been given: it answers in the page's language
    if (s.demo && s.demo.id !== prev.demo?.id) applyLanguage();
  });
  (window as unknown as { __NANOMUSE__?: typeof host }).__NANOMUSE__ = host;
}
