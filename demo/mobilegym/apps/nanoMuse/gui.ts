import PackageManagerService from '@/os/PackageManagerService';
import { domToPng } from 'modern-screenshot';
import { stage } from './stage';
import { t } from './res/strings';

/**
 * The simulated phone as a device NanoLika can operate.
 *
 * The server asks for two things over the notification bridge's WebSocket (see
 * `nanomuse.phone.link` on the Python side): the current *screen* — a screenshot, which app is
 * open, the screen size, whether the keyboard is up, and the elements that say or do something
 * — and one *action* — tap, type, swipe, back, home, open an app — by coordinates. That is the
 * contract the Android app fulfils with `AccessibilityService.takeScreenshot()`, the
 * accessibility tree and `dispatchGesture()`, and it is kept the same way here:
 *
 * - The screenshot is rendered in the tab from the phone's DOM (`#root`, 360×800) with
 *   modern-screenshot at twice the phone's pixels, 720×1600 — the width the Android app sends
 *   (it downscales to 720) — so Chinese labels are legible to the screen model; coordinates come
 *   back in that space and are halved here, as the Android app scales its own back up.
 * - `nodes` is read from the DOM the way the Android app reads the accessibility tree: the
 *   fields, buttons, links and texts that are on screen, each with its words, its centre and its
 *   flags, at most 120, fields first.
 * - Actions go through MobileGym's `__SIM_INPUT__` / `__OS__` runtime API, the same gestures
 *   its benchmark dispatches, so a tap lands the way a finger would and the apps animate as
 *   they do for a person. A password or code field is never typed into: the capsule asks the
 *   person to do that part (*Your turn*, **Continue**), as the Android app does.
 * - What a person sees meanwhile — the capsule, the ring where the finger is about to land,
 *   the glow along the edges — is `stage.ts`, left out of the screenshot.
 */

const PHONE_WIDTH = 360;
const PHONE_HEIGHT = 800;
/** Rendered pixels per phone pixel in the screenshot (and the space the server taps in). */
const SHOT_SCALE = 2;
/** How long to let the UI settle after an action before reading the screen again. */
const SETTLE_MS = 650;
/** The ring shows where the finger will land this long before it does, so the eye gets there first. */
const LEAD_MS = 360;
/** Timing that looks like a person, not a script (MobileGym's benchmark uses the same ranges). */
const TAP_GAP_MS = 120;
const TYPE_MS_PER_CHAR = 40;
const SWIPE_MS = 320;
const MAX_NODES = 120;

export interface ScreenNode {
  id: string;
  class: string;
  text?: string;
  desc?: string;
  hint?: string;
  res?: string;
  cx: number;
  cy: number;
  box: [number, number, number, number];
  clickable?: boolean;
  long_clickable?: boolean;
  editable?: boolean;
  password?: boolean;
  checked?: boolean;
  scrollable?: boolean;
  focused?: boolean;
  selected?: boolean;
  disabled?: boolean;
}

export interface ScreenPayload {
  app: string;
  app_name: string;
  route: string;
  width: number;
  height: number;
  keyboard: boolean;
  /** base64 PNG of the phone, `width`×`height` */
  screenshot: string | null;
  nodes?: ScreenNode[];
  note?: string;
}

export interface ActParams {
  action: string;
  x?: number;
  y?: number;
  x2?: number;
  y2?: number;
  label?: string;
  direction?: 'up' | 'down' | 'left' | 'right';
  distance?: number;
  text?: string;
  clear?: boolean;
  submit?: boolean;
  app?: string;
  seconds?: number;
}

export interface ActResult {
  note: string;
  screen: ScreenPayload;
}

export interface AppInfo {
  id: string;
  name: string;
}

// MobileGym's runtime globals (declared loosely: the simulator owns their types).
interface SimInput {
  tap: (x: number, y: number) => void;
  doubleTap: (x: number, y: number) => void;
  longPress: (x: number, y: number, ms?: number) => Promise<void>;
  type: (text: string, opts?: { clear?: boolean; perCharMs?: number }) => Promise<void>;
  swipe: (
    start: { x: number; y: number },
    end: { x: number; y: number },
    opts?: { ms?: number; steps?: number; inertia?: boolean },
  ) => Promise<void>;
  back: () => void;
  home: () => void;
  recent: () => void;
  enter: () => void;
}
interface SimOs {
  getAppRoute?: () => { app: string | null; path: string } | null;
  launchApp?: (id: string) => void;
  openApp?: (id: string, path?: string) => void;
  goHome?: () => void;
  keyboard?: { isVisible?: () => boolean };
}

function simInput(): SimInput {
  const api = (window as unknown as { __SIM_INPUT__?: SimInput }).__SIM_INPUT__;
  if (!api) throw new Error('the simulator input API (__SIM_INPUT__) is not available');
  return api;
}
function simOs(): SimOs {
  return (window as unknown as { __OS__?: SimOs }).__OS__ ?? {};
}

/** The screen as announced to the server: the picture's pixels. */
export const SCREEN = { width: PHONE_WIDTH * SHOT_SCALE, height: PHONE_HEIGHT * SHOT_SCALE };

/** Every installed app, as the agent may name it. */
export function installedApps(): AppInfo[] {
  try {
    return PackageManagerService.getInstalledPackages().map((m) => ({
      id: String(m.id),
      name: String(m.displayName || m.id),
    }));
  } catch {
    return [];
  }
}

/** The app id for a name the agent used (id, display name, alias, or a substring of one). */
export function resolveApp(wanted: string): string | null {
  const q = wanted.trim().toLowerCase();
  if (!q) return null;
  let packages: readonly { id: string; displayName: string; displayNameEn?: string; aliases?: string[] }[] = [];
  try {
    packages = PackageManagerService.getInstalledPackages() as typeof packages;
  } catch {
    return wanted;
  }
  const names = (p: (typeof packages)[number]) =>
    [p.id, p.displayName, p.displayNameEn ?? '', ...(p.aliases ?? [])].map((n) => String(n).toLowerCase());
  const exact = packages.find((p) => names(p).includes(q));
  if (exact) return String(exact.id);
  const partial = packages.find((p) => names(p).some((n) => n && (n.includes(q) || q.includes(n))));
  return partial ? String(partial.id) : null;
}

// ------------------------------------------------------------------ geometry

function phoneRoot(): HTMLElement {
  const root = document.getElementById('root');
  if (!root) throw new Error('the simulator root (#root) is not on the page');
  return root;
}

interface Frame {
  left: number;
  top: number;
  scale: number;
}

function frame(): Frame {
  const rect = phoneRoot().getBoundingClientRect();
  const scale = rect.width > 0 ? rect.width / PHONE_WIDTH : 1;
  return { left: rect.left, top: rect.top, scale };
}

/** Phone coordinates → browser viewport pixels (what `__SIM_INPUT__` wants). */
function toViewport(f: Frame, x: number, y: number): { x: number; y: number } {
  return { x: f.left + x * f.scale, y: f.top + y * f.scale };
}

const clampX = (v: number) => Math.min(PHONE_WIDTH - 1, Math.max(0, v));
const clampY = (v: number) => Math.min(PHONE_HEIGHT - 1, Math.max(0, v));

// ------------------------------------------------------------------ the screenshot

const OVERLAY_ATTR = 'data-muse-overlay';

/**
 * Render the phone to a PNG. `#root` carries the page's `transform: scale(...)`, which the
 * clone must not (the picture is the phone at its own size); the agent's overlay is left out.
 */
export async function screenshot(): Promise<string | null> {
  const root = phoneRoot();
  const rootRect = root.getBoundingClientRect();
  // Only what is inside the phone gets cloned: a station list or a chat history has thousands
  // of nodes scrolled out of view, and copying their styles is what makes a capture slow.
  const visible = (node: Node): boolean => {
    if (!(node instanceof Element)) return true;
    if (node.hasAttribute(OVERLAY_ATTR)) return false;
    if (node === root || node.tagName === 'HEAD' || node.tagName === 'STYLE') return true;
    const r = node.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) {
      // display:contents, inline wrappers, svg defs and a zero-height anchor whose children
      // overflow it (WeChat's tab bar hangs from one) have no area and must stay; a `display:
      // none` subtree has none either and must go: the shell keeps every backgrounded app
      // mounted that way, and cloning a hidden 12306 result list or a WeChat history on each
      // capture is what froze the phone for minutes once a second app had been opened.
      return getComputedStyle(node).display !== 'none';
    }
    return r.right > rootRect.left && r.left < rootRect.right && r.bottom > rootRect.top && r.top < rootRect.bottom;
  };
  try {
    const started = performance.now();
    const dataUrl = await domToPng(root, {
      width: PHONE_WIDTH,
      height: PHONE_HEIGHT,
      scale: SHOT_SCALE,
      backgroundColor: '#000',
      style: { transform: 'none', transition: 'none', margin: '0', inset: 'auto', position: 'static' },
      filter: visible,
      // fonts are the page's own; the SVG cannot see them unless embedded
      timeout: 8000,
    });
    lastCaptureMs = Math.round(performance.now() - started);
    const comma = dataUrl.indexOf(',');
    return comma >= 0 ? dataUrl.slice(comma + 1) : null;
  } catch (err) {
    console.warn('[NanoLika] screenshot failed', err);
    return null;
  }
}

/** How long the last capture took, for the console and the tests. */
export let lastCaptureMs = 0;

// ------------------------------------------------------------------ the elements

const INTERACTIVE_TAGS = new Set(['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT', 'SUMMARY', 'LABEL', 'OPTION']);
const INTERACTIVE_ROLES = new Set([
  'button',
  'link',
  'tab',
  'menuitem',
  'menuitemcheckbox',
  'menuitemradio',
  'checkbox',
  'switch',
  'radio',
  'option',
  'textbox',
  'searchbox',
  'combobox',
  'slider',
  'listitem',
]);
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'PATH', 'G', 'DEFS', 'USE', 'CIRCLE', 'RECT', 'LINE', 'POLYLINE', 'POLYGON', 'CANVAS', 'VIDEO', 'AUDIO', 'IFRAME', 'BR', 'HR']);
const SECRET = /密码|验证码|口令|password|passcode|passphrase|one-time|otp|verification code|security code|pin\b/i;

const squeeze = (s: string) => s.replace(/\s+/g, ' ').trim();

function ownText(el: Element): string {
  let out = '';
  for (const child of Array.from(el.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) out += child.textContent ?? '';
  }
  return squeeze(out);
}

/** Whether the field is for a password or a code: typed by the person, never by the hands. */
export function isSecretField(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement) {
    if (el.type === 'password') return true;
    const words = [el.name, el.placeholder, el.autocomplete, el.getAttribute('aria-label') ?? '', el.id].join(' ');
    return SECRET.test(words) || el.autocomplete === 'one-time-code';
  }
  if (el instanceof HTMLTextAreaElement) return SECRET.test([el.name, el.placeholder, el.getAttribute('aria-label') ?? ''].join(' '));
  return false;
}

function kindOf(el: Element, role: string, editable: boolean): string {
  const tag = el.tagName;
  if (editable) return 'EditText';
  if (tag === 'INPUT') {
    const type = (el as HTMLInputElement).type;
    if (type === 'checkbox') return role === 'switch' ? 'Switch' : 'CheckBox';
    if (type === 'radio') return 'RadioButton';
    if (type === 'range') return 'SeekBar';
    return 'Button';
  }
  if (role === 'switch') return 'Switch';
  if (role === 'checkbox' || role === 'menuitemcheckbox') return 'CheckBox';
  if (role === 'radio' || role === 'menuitemradio') return 'RadioButton';
  if (role === 'tab') return 'Tab';
  if (tag === 'A' || role === 'link') return 'Link';
  if (tag === 'BUTTON' || role === 'button' || role === 'menuitem') return 'Button';
  if (tag === 'IMG' || role === 'img') return 'ImageView';
  if (tag === 'SELECT' || role === 'combobox') return 'Spinner';
  return 'TextView';
}

/** An index path from the phone's root: stable while the tree is. */
function indexPath(el: Element, root: Element): string {
  const parts: number[] = [];
  let node: Element | null = el;
  while (node && node !== root) {
    const parent: Element | null = node.parentElement;
    if (!parent) break;
    parts.unshift(Array.prototype.indexOf.call(parent.children, node));
    node = parent;
  }
  return parts.join('.');
}

/**
 * The elements that say or do something, in the picture's pixel space — what the Android app
 * sends from the accessibility tree. The DOM stands in for the tree: fields, buttons, links,
 * things with a role or a pointer cursor, and text leaves; what is off screen, invisible or
 * under something else (a sheet, a dialog, the shade) is left out.
 */
export function collectNodes(): ScreenNode[] {
  const root = phoneRoot();
  const rootRect = root.getBoundingClientRect();
  const scale = rootRect.width > 0 ? rootRect.width / PHONE_WIDTH : 1;
  const active = document.activeElement;
  const fields: ScreenNode[] = [];
  const actives: ScreenNode[] = [];
  const texts: ScreenNode[] = [];
  const claimed = new Map<Element, string>(); // interactive element → its words (to skip the same text inside it)
  const all = root.querySelectorAll('*');
  for (const el of Array.from(all)) {
    if (fields.length + actives.length + texts.length >= MAX_NODES * 2) break;
    if (SKIP_TAGS.has(el.tagName.toUpperCase())) continue;
    if (el.closest(`[${OVERLAY_ATTR}]`)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.right <= rootRect.left || r.left >= rootRect.right || r.bottom <= rootRect.top || r.top >= rootRect.bottom) continue;
    const role = (el.getAttribute('role') ?? '').toLowerCase();
    const tag = el.tagName;
    const input = el instanceof HTMLInputElement ? el : null;
    const editable =
      (input !== null && !['button', 'submit', 'reset', 'checkbox', 'radio', 'range', 'file', 'hidden', 'image', 'color'].includes(input.type)) ||
      tag === 'TEXTAREA' ||
      (el as HTMLElement).isContentEditable ||
      role === 'textbox' ||
      role === 'searchbox';
    const label = el.getAttribute('aria-label') ?? el.getAttribute('title') ?? (el instanceof HTMLImageElement ? el.alt : '') ?? '';
    const hint = (el as HTMLInputElement).placeholder ?? '';
    const own = ownText(el);
    let interactive =
      editable ||
      INTERACTIVE_TAGS.has(tag) ||
      INTERACTIVE_ROLES.has(role) ||
      el.hasAttribute('onclick') ||
      (el.hasAttribute('tabindex') && Number(el.getAttribute('tabindex')) >= 0);
    let style: CSSStyleDeclaration | null = null;
    if (!interactive && !own && !label) {
      // a plain box: only worth a look if it scrolls or is clickable by its cursor
      style = getComputedStyle(el);
      if (style.cursor !== 'pointer') {
        const scrolls = /(auto|scroll)/.test(style.overflowY + style.overflowX) && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2);
        if (!scrolls) continue;
      } else interactive = true;
    }
    style = style ?? getComputedStyle(el);
    if (style.visibility === 'hidden' || Number(style.opacity) < 0.05 || style.pointerEvents === 'none' && !own && !label) continue;
    if (!interactive && style.cursor === 'pointer') interactive = true;
    // the words: a text leaf's own text, an interactive element's whole text when short
    let text = own;
    if (!text && interactive) {
      const whole = squeeze(el.textContent ?? '');
      if (whole && whole.length <= 60) text = whole;
    }
    if (input && (input.type === 'text' || input.type === 'search' || input.type === 'email' || input.type === 'tel' || input.type === 'url' || input.type === 'number') && input.value) {
      text = input.value;
    }
    if (!text && !label && !hint && !interactive && !editable) {
      const scrolls = /(auto|scroll)/.test(style.overflowY + style.overflowX) && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2);
      if (!scrolls) continue;
    }
    // the same words inside something already listed as clickable: one entry is enough
    if (!interactive && text) {
      let p: Element | null = el.parentElement;
      let dup = false;
      while (p && p !== root) {
        const words = claimed.get(p);
        if (words !== undefined) {
          dup = words === text || words.includes(text);
          break;
        }
        p = p.parentElement;
      }
      if (dup) continue;
    }
    // what is under something else — a sheet, a dialog, the shade — is not on the screen
    const cxv = Math.min(rootRect.right - 1, Math.max(rootRect.left, r.left + r.width / 2));
    const cyv = Math.min(rootRect.bottom - 1, Math.max(rootRect.top, r.top + r.height / 2));
    const hit = document.elementFromPoint(cxv, cyv);
    if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) continue;
    const left = Math.max(0, (r.left - rootRect.left) / scale);
    const top = Math.max(0, (r.top - rootRect.top) / scale);
    const right = Math.min(PHONE_WIDTH, (r.right - rootRect.left) / scale);
    const bottom = Math.min(PHONE_HEIGHT, (r.bottom - rootRect.top) / scale);
    const node: ScreenNode = {
      id: indexPath(el, root),
      class: kindOf(el, role, editable),
      cx: Math.round(((left + right) / 2) * SHOT_SCALE),
      cy: Math.round(((top + bottom) / 2) * SHOT_SCALE),
      box: [Math.round(left * SHOT_SCALE), Math.round(top * SHOT_SCALE), Math.round(right * SHOT_SCALE), Math.round(bottom * SHOT_SCALE)],
    };
    if (text) node.text = text.slice(0, 80);
    if (label && label !== text) node.desc = squeeze(label).slice(0, 80);
    if (hint) node.hint = squeeze(hint).slice(0, 80);
    if (el.id) node.res = el.id.slice(0, 80);
    if (interactive) node.clickable = true;
    if (editable) node.editable = true;
    if (editable && isSecretField(el)) node.password = true;
    if (input && (input.type === 'checkbox' || input.type === 'radio')) node.checked = input.checked;
    else if (el.hasAttribute('aria-checked')) node.checked = el.getAttribute('aria-checked') === 'true';
    else if (el.hasAttribute('aria-pressed')) node.checked = el.getAttribute('aria-pressed') === 'true';
    if (/(auto|scroll)/.test(style.overflowY + style.overflowX) && (el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2)) node.scrollable = true;
    if (active === el || (active && el.contains(active) && editable)) node.focused = true;
    if (el.getAttribute('aria-selected') === 'true' || el.hasAttribute('aria-current')) node.selected = true;
    if ((el as HTMLButtonElement).disabled === true || el.getAttribute('aria-disabled') === 'true') node.disabled = true;
    if (interactive) claimed.set(el, text);
    (editable ? fields : interactive ? actives : texts).push(node);
  }
  return [...fields, ...actives, ...texts].slice(0, MAX_NODES);
}

// ------------------------------------------------------------------ the screen

/** Read the phone's screen: the picture, the elements, and what the simulator can say about it. */
export async function readScreen(quiet = false): Promise<ScreenPayload> {
  const os = simOs();
  const route = os.getAppRoute?.() ?? null;
  const appId = route?.app ?? '';
  const apps = installedApps();
  const appName = appId ? apps.find((a) => a.id === appId)?.name ?? appId : 'Home';
  if (!quiet) stage.screen();
  let nodes: ScreenNode[] = [];
  try {
    nodes = collectNodes();
  } catch (err) {
    console.warn('[NanoLika] elements could not be read', err);
  }
  const shot = await screenshot();
  return {
    app: appId || 'launcher',
    app_name: appName,
    route: route?.path ?? '',
    width: SCREEN.width,
    height: SCREEN.height,
    keyboard: Boolean(os.keyboard?.isVisible?.()),
    screenshot: shot,
    nodes,
    note: shot ? undefined : 'the screenshot could not be rendered',
  };
}

// ------------------------------------------------------------------ acting

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The server's coordinates (the picture's pixels) → phone pixels. */
function toPhone(params: ActParams): ActParams {
  const p = { ...params };
  for (const k of ['x', 'y', 'x2', 'y2'] as const) {
    if (typeof p[k] === 'number') p[k] = (p[k] as number) / SHOT_SCALE;
  }
  return p;
}

function point(params: ActParams): { x: number; y: number } {
  if (typeof params.x === 'number' && typeof params.y === 'number') {
    return { x: clampX(params.x), y: clampY(params.y) };
  }
  throw new Error(`\`${params.action}\` needs x and y`);
}

/** The element a tap at phone coordinates would land on. */
function elementAt(f: Frame, x: number, y: number): Element | null {
  const v = toViewport(f, x, y);
  const hit = document.elementFromPoint(v.x, v.y);
  return hit && phoneRoot().contains(hit) ? hit : null;
}

/** One action, then the screen as it looks afterwards. */
export async function act(raw: ActParams): Promise<ActResult> {
  const params = toPhone(raw);
  const f = frame();
  const input = simInput();
  const os = simOs();
  const label = params.label?.trim() ?? '';
  let note = 'ok';
  switch (params.action) {
    case 'tap':
    case 'double_tap': {
      const p = point(params);
      stage.act(params.action, label, p);
      await sleep(LEAD_MS);
      const v = toViewport(f, p.x, p.y);
      if (params.action === 'tap') input.tap(v.x, v.y);
      else input.doubleTap(v.x, v.y);
      break;
    }
    case 'long_press': {
      const p = point(params);
      const ms = Math.round(Math.min(5, Math.max(0.4, Number(params.seconds ?? 0.8))) * 1000);
      stage.act(params.action, label, p, undefined, { holdMs: ms });
      await sleep(LEAD_MS);
      const v = toViewport(f, p.x, p.y);
      await input.longPress(v.x, v.y, ms);
      break;
    }
    case 'type': {
      const text = String(params.text ?? '');
      const hasPoint = typeof params.x === 'number' && typeof params.y === 'number';
      const p = hasPoint ? point(params) : undefined;
      // a password or a code is the person's to type: the capsule asks, the hands wait
      const target = p ? elementAt(f, p.x, p.y) : document.activeElement;
      if (isSecretField(target)) {
        const went = await stage.takeOver(t().hands_secret_field);
        note = went
          ? 'that is a password or code field: the person was asked to fill it in and tapped Continue — go on from the screen as it is now'
          : 'that is a password or code field: the person was asked to fill it in but did not continue; ask them in the chat';
        break;
      }
      stage.act('type', label, p, undefined, { text });
      if (p) {
        // focus the field first, like a finger would
        await sleep(LEAD_MS);
        const v = toViewport(f, p.x, p.y);
        input.tap(v.x, v.y);
        await sleep(TAP_GAP_MS);
      }
      await input.type(text, { clear: Boolean(params.clear), perCharMs: TYPE_MS_PER_CHAR });
      if (params.submit) {
        await sleep(TAP_GAP_MS);
        input.enter();
      }
      break;
    }
    case 'swipe': {
      let start: { x: number; y: number };
      let end: { x: number; y: number };
      if (typeof params.x2 === 'number' && typeof params.y2 === 'number') {
        start = point(params);
        end = { x: clampX(params.x2), y: clampY(params.y2) };
      } else {
        const dir = params.direction ?? 'up';
        const dist = Math.min(0.9, Math.max(0.1, params.distance ?? 0.5));
        const cx = typeof params.x === 'number' ? params.x : PHONE_WIDTH / 2;
        const cy = typeof params.y === 'number' ? params.y : PHONE_HEIGHT / 2;
        const dy = dir === 'up' || dir === 'down' ? dist * PHONE_HEIGHT : 0;
        const dx = dir === 'left' || dir === 'right' ? dist * PHONE_WIDTH : 0;
        const sign = dir === 'up' || dir === 'left' ? 1 : -1;
        const edge = (v: number, max: number) => Math.min(max - 8, Math.max(8, v));
        start = { x: edge(cx + (sign * dx) / 2, PHONE_WIDTH), y: edge(cy + (sign * dy) / 2, PHONE_HEIGHT) };
        end = { x: edge(cx - (sign * dx) / 2, PHONE_WIDTH), y: edge(cy - (sign * dy) / 2, PHONE_HEIGHT) };
      }
      // a scroll by direction is captioned as one ("Scroll up"), as on Android; a swipe between two points as a swipe
      stage.act('swipe', label || (typeof params.x2 === 'number' ? '' : t().hands_scroll(params.direction ?? 'up')), start, end);
      await sleep(LEAD_MS);
      await input.swipe(toViewport(f, start.x, start.y), toViewport(f, end.x, end.y), { ms: SWIPE_MS, inertia: true });
      break;
    }
    case 'enter':
      stage.act('enter', label);
      input.enter();
      break;
    case 'back':
      stage.act('back', label);
      input.back();
      break;
    case 'home':
      stage.act('home', label);
      input.home();
      break;
    case 'recents':
      stage.act('recents', label);
      input.recent();
      break;
    case 'open_app': {
      const id = resolveApp(String(params.app ?? ''));
      if (!id) throw new Error(`no app called '${params.app}' on this phone`);
      stage.act('open_app', label || t().hands_open_app(installedApps().find((a) => a.id === id)?.name ?? String(params.app ?? '')));
      if (os.launchApp) os.launchApp(id);
      else if (os.openApp) os.openApp(id);
      else throw new Error('the simulator cannot open apps (__OS__.launchApp missing)');
      note = `opened ${id}`;
      await sleep(400);
      break;
    }
    case 'wait': {
      const seconds = Math.min(10, Math.max(0.2, Number(params.seconds ?? 1)));
      stage.act('wait', label || t().hands_wait(Math.round(seconds)));
      await sleep(seconds * 1000);
      note = `waited ${seconds}s`;
      break;
    }
    default:
      throw new Error(`unknown action '${params.action}'`);
  }
  await sleep(SETTLE_MS);
  return { note, screen: await readScreen() };
}

// A handle for the console and for tests, the way MobileGym exposes __SIM_INPUT__ / __OS__.
(window as unknown as { __MUSE_GUI__?: unknown }).__MUSE_GUI__ = {
  readScreen,
  act,
  screenshot,
  collectNodes,
  stage,
  captureMs: () => lastCaptureMs,
};
