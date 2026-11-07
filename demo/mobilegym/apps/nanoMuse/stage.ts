import { getLocale } from '@/os/locale';
import { t } from './res/strings';

/**
 * What a person watching the phone sees of the hands: the same picture the Android app and
 * the desktop stage draw (`io.github.nanomuse.hands.HandsCapsule` / `HandsStage`,
 * `desktop/app/src/renderer/stage/`), inside the simulated phone.
 *
 * - The **capsule**: a dark pill at the top with the agent's face in a slowly turning three-hue
 *   ring, four activity bars, what is being done ("Step 3 · tap the search box"), and **Stop**.
 *   When the hands need the user it grows into a card — *Your turn* with **Continue** for a
 *   password field, *Waiting for your approval* with **Allow once / Deny** for a tap that
 *   needs a yes (decided right here, `POST /api/approvals/{id}` through the bridge; the
 *   person is never sent back into the NanoLika app for it), *Your turn — <reason>* or *You
 *   have the phone* with **Done** while a hold is on (the agent handed the phone over, or the
 *   person took it; `POST /api/holds/{id}/done`), the agent's question with **Open** when the
 *   task ends asking — and the ring stands still. *Done* in green and *Stopped* in red for a
 *   moment, then it slides away.
 * - The **stage**: a glow breathing along the screen's edges for as long as the run is on —
 *   blue while the hands work, amber while they wait — with one light running round the rim;
 *   a sweep across the screen for the moment of a screenshot; and, at the point the model
 *   chose, a ring with a turning arc, two ripples and the action's words in a chip beside it,
 *   a moment before the finger lands so the eye gets there first; a comet from the previous
 *   point; a dashed line with a travelling dot and an arrow for a swipe; a chip under the
 *   capsule counting the characters typed.
 *
 * Everything lives inside `#root` (so it scales with the phone) and carries `data-muse-overlay`
 * (so the screenshot leaves it out: the model never sees any of it and cannot tap its own Stop
 * button). Only the capsule's buttons take a touch; the rest lets touches — and the injected
 * gestures — through. A capsule in the way of a tap moves to the bottom for that step.
 *
 * The capsule follows the server's `task` events (`begin` / `end` / `notice`, bridge.ts). An
 * `end` can fail to arrive — the socket dropped just then, the server gave up on the task — so
 * it never relies on one alone: the bridge sets it from the server's word on reconnect
 * (`reset` / `begin`), and a capsule left "working" with nothing from the server for
 * `STALE_MS` comes down by itself rather than stay on its last step for good.
 */

const PHONE_WIDTH = 360;
const PHONE_HEIGHT = 800;
const STATUS_BAR = 40; // MobileGym's status bar (os/data/simulatorConfig.ts)
const OVERLAY_ATTR = 'data-muse-overlay';
const ACCENT = '#0A66E4';
const CYAN = '#06B6D4';
const RING_MS = 1300;
const TRAIL_MS = 720;
/**
 * A task that is on has the server asking for the screen or an action every step; "working"
 * with nothing at all for this long means the task is gone and its end never got here. Long
 * enough for the slowest model turn (one step is seconds, a stuck one a minute or two).
 */
const STALE_MS = 180_000;
const FONT = '600 11.5px -apple-system, "PingFang SC", "Noto Sans CJK SC", "Segoe UI", system-ui, sans-serif';

export type PillState = 'working' | 'turn' | 'approval' | 'notice' | 'done' | 'stopped';

interface Mark {
  x: number;
  y: number;
  x2?: number;
  y2?: number;
  label: string;
  born: number;
  life: number;
  kind: 'point' | 'drag' | 'trail' | 'hold';
}

const CSS = `
@property --muse-a { syntax: "<angle>"; initial-value: 0deg; inherits: false; }
.muse-stage { position:absolute; inset:0; z-index:2147483000; pointer-events:none; overflow:hidden;
  font-family:-apple-system,"PingFang SC","Noto Sans CJK SC","Segoe UI",system-ui,sans-serif; -webkit-font-smoothing:antialiased;
  --accent:#0a66e4; --violet:#7c5cff; --cyan:#06b6d4; --ink:rgba(17,18,24,.84); }
.muse-stage canvas { position:absolute; left:0; top:0; width:${PHONE_WIDTH}px; height:${PHONE_HEIGHT}px; }
/* ---- the rim */
.muse-frame { position:absolute; inset:0; opacity:0; transition:opacity 480ms ease; --lap:8s;
  --c1:var(--accent); --c2:var(--violet); --c3:var(--cyan); --dim:rgba(10,102,228,.2); --head:#eef7ff; --haze:rgba(124,92,255,.28); }
.muse-frame.wait { --c1:#d97706; --c2:#f59e0b; --c3:#fbbf24; --dim:rgba(245,158,11,.22); --head:#fff6dc; --haze:rgba(245,158,11,.26); }
.muse-frame.on { opacity:1; }
.muse-frame .e, .muse-frame .g { position:absolute; background-repeat:no-repeat; animation:var(--lap) linear infinite; }
.muse-frame .e { background-color:var(--dim); }
.muse-frame .e.t, .muse-frame .e.b { left:0; right:0; height:3px; background-size:200% 100%; }
.muse-frame .e.l, .muse-frame .e.r { top:0; bottom:0; width:3px; background-size:100% 200%; }
.muse-frame .g.t, .muse-frame .g.b { left:0; right:0; height:22px; background-size:200% 100%; }
.muse-frame .g.l, .muse-frame .g.r { top:0; bottom:0; width:22px; background-size:100% 200%; }
.muse-frame .g.t { top:0; background-image:linear-gradient(90deg,transparent 0%,transparent 62%,var(--haze) 100%); animation-name:muse-run-x; }
.muse-frame .g.r { right:0; background-image:linear-gradient(180deg,transparent 0%,transparent 62%,var(--haze) 100%); animation-name:muse-run-y; animation-delay:calc(var(--lap) * -0.75); }
.muse-frame .g.b { bottom:0; background-image:linear-gradient(270deg,transparent 0%,transparent 62%,var(--haze) 100%); animation-name:muse-run-x-back; animation-delay:calc(var(--lap) * -0.5); }
.muse-frame .g.l { left:0; background-image:linear-gradient(0deg,transparent 0%,transparent 62%,var(--haze) 100%); animation-name:muse-run-y-back; animation-delay:calc(var(--lap) * -0.25); }
.muse-frame .e.t { top:0; background-image:linear-gradient(90deg,var(--dim) 0%,var(--dim) 45%,var(--c1) 72%,var(--c2) 88%,var(--c3) 97%,var(--head) 100%); animation-name:muse-run-x; }
.muse-frame .e.r { right:0; background-image:linear-gradient(180deg,var(--dim) 0%,var(--dim) 45%,var(--c1) 72%,var(--c2) 88%,var(--c3) 97%,var(--head) 100%); animation-name:muse-run-y; animation-delay:calc(var(--lap) * -0.75); }
.muse-frame .e.b { bottom:0; background-image:linear-gradient(270deg,var(--dim) 0%,var(--dim) 45%,var(--c1) 72%,var(--c2) 88%,var(--c3) 97%,var(--head) 100%); animation-name:muse-run-x-back; animation-delay:calc(var(--lap) * -0.5); }
.muse-frame .e.l { left:0; background-image:linear-gradient(0deg,var(--dim) 0%,var(--dim) 45%,var(--c1) 72%,var(--c2) 88%,var(--c3) 97%,var(--head) 100%); animation-name:muse-run-y-back; animation-delay:calc(var(--lap) * -0.25); }
@keyframes muse-run-x { 0%{background-position-x:200%} 50%{background-position-x:0%} 100%{background-position-x:0%} }
@keyframes muse-run-y { 0%{background-position-y:200%} 50%{background-position-y:0%} 100%{background-position-y:0%} }
@keyframes muse-run-x-back { 0%{background-position-x:-100%} 50%{background-position-x:100%} 100%{background-position-x:100%} }
@keyframes muse-run-y-back { 0%{background-position-y:-100%} 50%{background-position-y:100%} 100%{background-position-y:100%} }
.muse-frame .h { position:absolute; --g:rgba(10,102,228,.15); }
.muse-frame.wait .h { --g:rgba(245,158,11,.14); }
.muse-frame .h.t { top:0; left:0; right:0; height:80px; background:linear-gradient(180deg,var(--g),transparent); }
.muse-frame .h.b { bottom:0; left:0; right:0; height:80px; background:linear-gradient(0deg,var(--g),transparent); }
.muse-frame .h.l { top:0; bottom:0; left:0; width:60px; background:linear-gradient(90deg,var(--g),transparent); }
.muse-frame .h.r { top:0; bottom:0; right:0; width:60px; background:linear-gradient(270deg,var(--g),transparent); }
.muse-frame .look-set { position:absolute; inset:0; opacity:0; transition:opacity 160ms ease; }
.muse-frame .look-set .h { --g:rgba(6,182,212,.3); }
.muse-frame.look .look-set { opacity:1; }
.muse-scan { position:absolute; left:0; right:0; top:-6px; height:3px; opacity:0;
  background:linear-gradient(90deg,transparent,rgba(6,182,212,.9),rgba(10,102,228,.9),transparent); box-shadow:0 0 18px rgba(6,182,212,.6); }
.muse-scan.go { animation:muse-sweep 620ms cubic-bezier(.4,0,.2,1) 1; }
@keyframes muse-sweep { 0%{opacity:0;transform:translateY(0)} 10%{opacity:1} 90%{opacity:1} 100%{opacity:0;transform:translateY(${PHONE_HEIGHT}px)} }
@keyframes muse-turn { to { --muse-a:360deg; } }
/* ---- the capsule */
.muse-pill { position:absolute; top:${STATUS_BAR + 6}px; left:50%; transform:translateX(-50%) translateY(-28px) scale(.96);
  display:flex; align-items:center; gap:8px; padding:7px 9px 7px 8px; border-radius:26px; background:var(--ink); color:#fff;
  font-size:12px; line-height:1.25; max-width:${PHONE_WIDTH - 20}px; box-sizing:border-box; pointer-events:auto;
  box-shadow:0 1px 0 rgba(255,255,255,.08) inset,0 10px 32px rgba(0,0,0,.32),0 0 0 1px rgba(255,255,255,.06);
  opacity:0; transition:opacity 260ms ease,transform 320ms cubic-bezier(.2,.8,.2,1),top 260ms ease,background-color 400ms ease; }
.muse-pill.on { opacity:1; transform:translateX(-50%) translateY(0) scale(1); }
.muse-pill.low { top:${PHONE_HEIGHT - 86}px; }
.muse-pill.done { background:rgba(6,95,70,.9); }
.muse-pill.stopped { background:rgba(127,29,29,.9); }
.muse-pill .face { position:relative; width:26px; height:26px; border-radius:50%; background:#f1efeb center/cover; flex:none; }
.muse-pill .face::after { content:""; position:absolute; inset:-4px; border-radius:50%; border:1.5px solid transparent;
  background:conic-gradient(from var(--muse-a),var(--accent),var(--violet),var(--cyan),var(--accent)) border-box;
  -webkit-mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0); -webkit-mask-composite:xor; mask-composite:exclude;
  animation:muse-turn 2.4s linear infinite; }
.muse-pill.still .face::after, .muse-pill.done .face::after, .muse-pill.stopped .face::after { animation:none; background:rgba(255,255,255,.35); }
.muse-pill .bars { display:flex; align-items:flex-end; gap:2.5px; height:14px; flex:none; }
.muse-pill .bars i { display:block; width:3px; border-radius:2px; background:linear-gradient(180deg,var(--cyan),var(--accent)); animation:muse-bar 1s ease-in-out infinite; height:40%; }
.muse-pill .bars i:nth-child(2) { animation-delay:.15s } .muse-pill .bars i:nth-child(3) { animation-delay:.3s } .muse-pill .bars i:nth-child(4) { animation-delay:.45s }
.muse-pill.still .bars, .muse-pill.done .bars, .muse-pill.stopped .bars { display:none; }
.muse-pill .mark { display:none; width:16px; height:16px; flex:none; }
.muse-pill.done .mark, .muse-pill.stopped .mark { display:block; }
.muse-pill .texts { display:flex; flex-direction:column; min-width:0; flex:1 1 auto; gap:1px; }
.muse-pill .title { font-weight:600; font-size:12.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.muse-pill .detail { color:rgba(255,255,255,.8); font-size:11.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.muse-pill .detail.swap { animation:muse-swap 260ms ease; }
.muse-pill.card { border-radius:18px; flex-wrap:wrap; padding:9px 10px 9px 9px; width:${PHONE_WIDTH - 20}px; }
.muse-pill.card .detail { white-space:normal; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; }
.muse-pill .actions { display:flex; gap:6px; flex:none; margin-left:auto; }
.muse-pill button { appearance:none; border:0; padding:5px 11px; border-radius:999px; background:rgba(255,255,255,.16); color:#fff;
  font:inherit; font-size:12px; font-weight:600; cursor:pointer; transition:background-color 160ms ease; pointer-events:auto; touch-action:manipulation; }
.muse-pill button.stop:hover, .muse-pill button.stop:active { background:rgba(255,90,80,.9); }
.muse-pill button.go { background:var(--accent); }
.muse-pill button.go:hover, .muse-pill button.go:active { background:#0b5ac9; }
.muse-pill button.deny { background:rgba(155,59,59,.95); }
.muse-pill button.deny:hover, .muse-pill button.deny:active { background:rgba(185,60,60,1); }
.muse-pill button:disabled { opacity:.55; cursor:default; }
.muse-pill.done button, .muse-pill.stopped button { display:none; }
@keyframes muse-bar { 0%,100%{height:35%} 50%{height:100%} }
@keyframes muse-swap { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:none} }
/* ---- typing */
.muse-keys { position:absolute; top:${STATUS_BAR + 56}px; left:50%; transform:translateX(-50%) translateY(-8px); display:flex; align-items:center; gap:6px;
  opacity:0; transition:opacity 200ms ease,transform 260ms cubic-bezier(.2,.8,.2,1); }
.muse-keys.on { opacity:1; transform:translateX(-50%) translateY(0); }
.muse-keys .typing { display:inline-flex; align-items:center; gap:7px; padding:6px 11px; border-radius:999px; background:var(--ink); color:#fff; font-size:11.5px; box-shadow:0 8px 24px rgba(0,0,0,.28); }
.muse-keys .typing .caret { width:2px; height:12px; background:var(--cyan); animation:muse-blink .9s steps(2) infinite; }
.muse-keys kbd { display:inline-flex; align-items:center; justify-content:center; min-width:26px; height:26px; padding:0 8px; border-radius:7px;
  background:linear-gradient(180deg,#fff,#e6e7ee); color:#1c1c1e; border:1px solid rgba(0,0,0,.14); font:600 12px -apple-system,"Segoe UI",system-ui,sans-serif;
  box-shadow:0 2px 0 #9ea2b3,0 6px 16px rgba(0,0,0,.28); }
@keyframes muse-blink { to { visibility:hidden; } }
`;

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c);
const easeOut = (v: number) => 1 - Math.pow(1 - v, 3);
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

export class Stage {
  private layer: HTMLDivElement | null = null;
  private frame!: HTMLDivElement;
  private scan!: HTMLDivElement;
  private canvas!: HTMLCanvasElement;
  private ctx!: CanvasRenderingContext2D;
  private pill!: HTMLDivElement;
  private faceEl!: HTMLSpanElement;
  private titleEl!: HTMLSpanElement;
  private detailEl!: HTMLSpanElement;
  private markPath!: SVGPathElement;
  private stopBtn!: HTMLButtonElement;
  private continueBtn!: HTMLButtonElement;
  private openBtn!: HTMLButtonElement;
  private allowBtn!: HTMLButtonElement;
  private denyBtn!: HTMLButtonElement;
  private keys!: HTMLDivElement;

  private marks: Mark[] = [];
  private drawing = false;
  private last: { x: number; y: number } | null = null;
  private pillTimer: ReturnType<typeof setTimeout> | null = null;
  private keysTimer: ReturnType<typeof setTimeout> | null = null;
  private lowTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private continueWaiters: Array<() => void> = [];
  /** The approval the card is for (`approval` event id); '' when none is on the capsule. */
  private approvalId = '';
  /** The hold the card is for (`hold` event id, contract C1); '' when none is on. */
  private holdId = '';
  private holdBy = '';
  private holdReason = '';

  /** Whether a task is on (between `begin` and `end`/`stopped`). */
  active = false;
  /** Actions since `begin`; the capsule's "Step N". */
  steps = 0;
  state: PillState = 'working';
  /** The agent's name and face, from the server's profile. */
  name = 'NanoLika';
  face = '';

  onStop: (() => void) | null = null;
  onOpen: (() => void) | null = null;
  /** The capsule came down by itself (nothing from the server for STALE_MS); `steps` says how far it got. */
  onGone: (() => void) | null = null;
  /** Allow once / Deny on the approval card: the bridge answers the server. */
  onAllow: ((approvalId: string) => void) | null = null;
  onDeny: ((approvalId: string) => void) | null = null;
  /** Done on a hold card: the bridge tells the server the person is finished. */
  onDone: ((holdId: string) => void) | null = null;

  // ------------------------------------------------------------------ building
  private ensure(): HTMLDivElement {
    if (this.layer && this.layer.isConnected) return this.layer;
    const root = document.getElementById('root');
    if (!root) throw new Error('the simulator root (#root) is not on the page');
    if (!document.getElementById('muse-stage-style')) {
      const style = document.createElement('style');
      style.id = 'muse-stage-style';
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    const layer = document.createElement('div');
    layer.className = 'muse-stage';
    layer.setAttribute(OVERLAY_ATTR, '');
    layer.innerHTML =
      '<div class="muse-frame">' +
      '<i class="h t"></i><i class="h b"></i><i class="h l"></i><i class="h r"></i>' +
      '<div class="look-set"><i class="h t"></i><i class="h b"></i><i class="h l"></i><i class="h r"></i></div>' +
      '<i class="g t"></i><i class="g b"></i><i class="g l"></i><i class="g r"></i>' +
      '<i class="e t"></i><i class="e b"></i><i class="e l"></i><i class="e r"></i>' +
      '</div>' +
      '<div class="muse-scan"></div>' +
      `<canvas width="${PHONE_WIDTH * 2}" height="${PHONE_HEIGHT * 2}"></canvas>` +
      '<div class="muse-pill" role="status">' +
      '<span class="face"></span>' +
      '<span class="bars"><i></i><i></i><i></i><i></i></span>' +
      '<svg class="mark" viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3 3 7-7"/></svg>' +
      '<span class="texts"><span class="title"></span><span class="detail"></span></span>' +
      '<span class="actions"><button type="button" class="go continue"></button><button type="button" class="go allow"></button><button type="button" class="deny"></button><button type="button" class="go open"></button><button type="button" class="stop"></button></span>' +
      '</div>' +
      '<div class="muse-keys"></div>';
    root.appendChild(layer);
    this.layer = layer;
    this.frame = layer.querySelector('.muse-frame') as HTMLDivElement;
    this.scan = layer.querySelector('.muse-scan') as HTMLDivElement;
    this.canvas = layer.querySelector('canvas') as HTMLCanvasElement;
    this.ctx = this.canvas.getContext('2d') as CanvasRenderingContext2D;
    this.ctx.setTransform(2, 0, 0, 2, 0, 0);
    this.pill = layer.querySelector('.muse-pill') as HTMLDivElement;
    this.faceEl = this.pill.querySelector('.face') as HTMLSpanElement;
    this.titleEl = this.pill.querySelector('.title') as HTMLSpanElement;
    this.detailEl = this.pill.querySelector('.detail') as HTMLSpanElement;
    this.markPath = this.pill.querySelector('.mark path') as SVGPathElement;
    this.stopBtn = this.pill.querySelector('button.stop') as HTMLButtonElement;
    this.continueBtn = this.pill.querySelector('button.continue') as HTMLButtonElement;
    this.openBtn = this.pill.querySelector('button.open') as HTMLButtonElement;
    this.allowBtn = this.pill.querySelector('button.allow') as HTMLButtonElement;
    this.denyBtn = this.pill.querySelector('button.deny') as HTMLButtonElement;
    this.keys = layer.querySelector('.muse-keys') as HTMLDivElement;
    // the simulator's gesture layer listens on pointer events; the buttons are plain clicks
    const quiet = (e: Event) => e.stopPropagation();
    for (const b of [this.stopBtn, this.continueBtn, this.openBtn, this.allowBtn, this.denyBtn]) {
      b.addEventListener('pointerdown', quiet);
      b.addEventListener('touchstart', quiet, { passive: true });
    }
    this.stopBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.onStop?.();
    });
    this.continueBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.personDone();
    });
    this.allowBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.decide(true);
    });
    this.denyBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.decide(false);
    });
    this.openBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.onOpen?.();
      this.hide();
    });
    this.applyFace();
    return layer;
  }

  /** The agent's face and name, as the capsule shows them. */
  setIdentity(name: string, face: string): void {
    this.name = name || 'NanoLika';
    this.face = face;
    if (this.layer?.isConnected) this.applyFace();
  }

  private applyFace(): void {
    if (!this.faceEl) return;
    this.faceEl.style.backgroundImage = this.face ? `url("${this.face}")` : '';
  }

  // ------------------------------------------------------------------ the capsule
  private setDetail(text: string): void {
    if (this.detailEl.textContent === text) return;
    this.detailEl.textContent = text;
    this.detailEl.classList.remove('swap');
    void this.detailEl.offsetWidth;
    this.detailEl.classList.add('swap');
  }

  private show(state: PillState, title: string, detail: string): void {
    this.ensure();
    if (this.pillTimer) {
      clearTimeout(this.pillTimer);
      this.pillTimer = null;
    }
    this.state = state;
    const waiting = state === 'turn' || state === 'approval' || state === 'notice';
    this.pill.classList.toggle('still', waiting);
    this.pill.classList.toggle('card', waiting);
    this.pill.classList.toggle('done', state === 'done');
    this.pill.classList.toggle('stopped', state === 'stopped');
    this.frame.classList.toggle('wait', waiting);
    this.markPath.setAttribute('d', state === 'stopped' ? 'M4 4l8 8M12 4l-8 8' : 'M3 8.5l3 3 7-7');
    this.titleEl.textContent = title;
    this.setDetail(detail);
    const s = t();
    this.stopBtn.textContent = s.hands_stop;
    // the one button of the "your turn" card: Continue for the password field the person
    // fills in here (the hands wait in this tab), Done for a hold the server is waiting on
    this.continueBtn.textContent = this.continueWaiters.length ? s.hands_continue : s.hands_hold_done;
    this.allowBtn.textContent = s.hands_allow_once;
    this.denyBtn.textContent = s.hands_deny;
    this.openBtn.textContent = s.hands_open;
    this.stopBtn.style.display = state === 'working' || state === 'turn' || state === 'approval' ? '' : 'none';
    this.continueBtn.style.display = state === 'turn' ? '' : 'none';
    this.allowBtn.style.display = state === 'approval' ? '' : 'none';
    this.denyBtn.style.display = state === 'approval' ? '' : 'none';
    this.allowBtn.disabled = false;
    this.denyBtn.disabled = false;
    // Open only for the question a finished task left: approvals are decided right here
    this.openBtn.style.display = state === 'notice' ? '' : 'none';
    this.pill.classList.add('on');
    this.frame.classList.add('on');
    this.watch();
  }

  /** Back to "working" when a task is on, down when none is: after a card is answered. */
  private settle(): void {
    if (this.active) this.show('working', t().hands_step(this.steps), t().hands_looking);
    else this.hide();
  }

  /** "Working" with nothing from the server for STALE_MS: the task is gone, and so is the capsule. */
  private watch(): void {
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = null;
    if (!this.active || this.state !== 'working') return;
    this.staleTimer = setTimeout(() => {
      this.staleTimer = null;
      if (!this.active || this.state !== 'working') return;
      console.info('[NanoLika] nothing from the server for a while: the task is taken as over');
      const went = this.steps > 0;
      this.reset();
      if (went) this.onGone?.();
    }, STALE_MS);
  }

  /** A task started: the capsule slides in. */
  begin(goal: string): void {
    this.active = true;
    this.steps = 0;
    this.marks = [];
    this.last = null;
    this.ensure();
    this.pill.classList.remove('low');
    const s = t();
    this.show('working', s.hands_working(this.name), goal ? goal : s.hands_looking);
    // the person has the phone (a hold from before the task): the card stays, the hands wait
    if (this.holdId) this.hold(this.holdId, this.holdBy, this.holdReason);
  }

  /** The screen is being read: a sweep, and the words. */
  screen(): void {
    if (!this.layer?.isConnected || !this.pill.classList.contains('on')) return;
    this.frame.classList.add('look');
    this.scan.classList.remove('go');
    void this.scan.offsetWidth;
    this.scan.classList.add('go');
    setTimeout(() => this.frame.classList.remove('look'), 380);
    if (this.active && this.state === 'working') {
      this.setDetail(t().hands_looking);
      this.watch(); // the server is there: the task is on
    }
  }

  /** One action: the words in the capsule, the mark on the screen. Phone coordinates. */
  act(action: string, label: string, p?: { x: number; y: number }, p2?: { x: number; y: number }, extra?: { text?: string; holdMs?: number }): void {
    const s = t();
    this.steps += 1;
    const words = label.trim() || s.hands_action(action);
    if (!this.active) {
      // a single act outside a task: the capsule comes for a moment
      this.show('working', s.hands_working(this.name), words);
      this.hideAfter(RING_MS + 600);
    } else {
      this.show('working', s.hands_step(this.steps), words);
    }
    if (p) {
      this.dodge(p.x, p.y);
      if (this.last && Math.hypot(this.last.x - p.x, this.last.y - p.y) > 36) {
        this.addMark({ x: this.last.x, y: this.last.y, x2: p.x, y2: p.y, label: '', kind: 'trail', life: TRAIL_MS });
      }
      this.last = p;
      const kind: Mark['kind'] = action === 'swipe' && p2 ? 'drag' : extra?.holdMs ? 'hold' : 'point';
      this.addMark({
        x: p.x,
        y: p.y,
        x2: p2?.x,
        y2: p2?.y,
        label: words,
        kind,
        life: kind === 'drag' ? RING_MS + 400 : kind === 'hold' ? (extra?.holdMs ?? 800) + 500 : RING_MS,
      });
      if (p2) this.last = p2;
    }
    if (action === 'type') {
      const n = extra?.text?.length ?? 0;
      this.showKeys(`<span class="typing"><span class="caret"></span>${esc(s.hands_typing(n))}</span>`, Math.min(4000, 900 + n * 45));
    } else if (action === 'enter' || action === 'back' || action === 'home' || action === 'recents') {
      this.showKeys(`<kbd>${esc(s.hands_key(action))}</kbd>`, 1200);
    }
  }

  /**
   * The hands need the user on the phone (a password field): resolves when Continue is
   * tapped — or when the server's hold for the same moment is closed from the chat (the
   * operator mirrors this pause as a `hold` event; the card here is one card for both).
   */
  takeOver(reason: string, timeoutMs = 120_000): Promise<boolean> {
    const s = t();
    return new Promise<boolean>((resolve) => {
      let done = false;
      const timer = setTimeout(() => finish(false), timeoutMs);
      const finish = (ok: boolean) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        this.continueWaiters = this.continueWaiters.filter((w) => w !== onContinue);
        if (!this.holdId) this.settle();
        resolve(ok);
      };
      const onContinue = () => finish(true);
      this.continueWaiters.push(onContinue);
      this.show('turn', s.hands_your_turn, reason || s.hands_your_turn_detail);
    });
  }

  /** The person tapped Continue / Done: the local wait ends, and the server's hold with it. */
  private personDone(): void {
    const waiters = this.continueWaiters;
    this.continueWaiters = [];
    const hold = this.holdId;
    this.holdId = '';
    for (const w of waiters) w();
    if (hold) {
      this.onDone?.(hold);
      // the server's `hold off` confirms it; until then the hands are taken as back at work
      this.settle();
    }
  }

  /**
   * A hold is on (contract C1): the agent handed the phone to the person (`by: "agent"` —
   * *Your turn — <reason>*) or the person took it (`by: "user"` — *You have the phone*);
   * **Done** ends it. A hold that arrives while the password card is already up is the same
   * pause seen from the server: no second card, Continue answers both.
   */
  hold(id: string, by: string, reason: string): void {
    this.holdId = id;
    this.holdBy = by;
    this.holdReason = reason;
    if (this.continueWaiters.length && this.state === 'turn') return;
    const s = t();
    if (by === 'user') this.show('turn', s.hands_you_have_phone, reason || s.hands_you_have_phone_detail);
    else this.show('turn', s.hands_your_turn, reason || s.hands_your_turn_done_detail);
  }

  /** The hold ended (Done here, in the chat, or the agent gave up waiting): on with the work. */
  holdOff(id: string): void {
    if (this.holdId !== id) return;
    this.holdId = '';
    // the chat's Done ends the password wait here too — the hands are not left waiting for
    // a Continue nobody will tap
    const waiters = this.continueWaiters;
    this.continueWaiters = [];
    for (const w of waiters) w();
    if (this.state === 'turn') this.settle();
  }

  /**
   * A tap waits for the person's yes: the card carries the request and **Allow once / Deny**,
   * decided here (the bridge answers `POST /api/approvals/{id}`). Nothing sends the person
   * back into the NanoLika app.
   */
  approval(id: string, what: string, purpose = ''): void {
    const s = t();
    this.approvalId = id;
    const detail = [what.trim(), purpose.trim() && s.hands_approval_for(purpose.trim())].filter(Boolean).join(' — ');
    this.show('approval', s.hands_approval_title, detail || s.hands_approval_detail_plain);
  }

  /** Allow once / Deny tapped: the buttons go quiet until the server's word comes back. */
  private decide(approved: boolean): void {
    const id = this.approvalId;
    if (!id) return;
    this.allowBtn.disabled = true;
    this.denyBtn.disabled = true;
    this.setDetail(approved ? t().hands_approval_allowing : t().hands_approval_denying);
    if (approved) this.onAllow?.(id);
    else this.onDeny?.(id);
  }

  /** The decision did not reach the server: the card is back for another go. */
  approvalFailed(id: string, what: string, purpose = ''): void {
    if (this.approvalId !== id || this.state !== 'approval') return;
    this.approval(id, what, purpose);
    this.setDetail(t().hands_approval_retry);
  }

  /** The approval was answered (here or elsewhere): on with the work. */
  resume(id = ''): void {
    if (this.state !== 'approval') return;
    if (id && this.approvalId && this.approvalId !== id) return;
    this.approvalId = '';
    this.settle();
  }

  /** The task ended asking something: the question stays, with Open. */
  notice(text: string): void {
    this.active = false;
    this.show('notice', t().hands_question(this.name), text);
  }

  /** The task ended: a green tick, then gone. Nothing when it already ended here (Stop). */
  end(): void {
    if (!this.active) return;
    this.active = false;
    this.approvalId = '';
    this.holdId = '';
    if (!this.layer?.isConnected || !this.pill.classList.contains('on')) return;
    this.show('done', this.name, t().hands_done);
    this.hideAfter(1100);
  }

  /** Stopped by the user: red, then gone. */
  stopped(): void {
    this.active = false;
    this.continueWaiters = [];
    this.approvalId = '';
    this.holdId = '';
    this.show('stopped', this.name, t().hands_stopped);
    this.hideAfter(1100);
  }

  /**
   * The task is gone without a word — the link said so, or the server's state on reconnect
   * did, or nothing came for too long: the capsule comes down quietly, no tick and no cross.
   */
  reset(): void {
    this.active = false;
    this.continueWaiters = [];
    this.approvalId = '';
    this.holdId = '';
    if (this.pillTimer) {
      clearTimeout(this.pillTimer);
      this.pillTimer = null;
    }
    if (this.staleTimer) {
      clearTimeout(this.staleTimer);
      this.staleTimer = null;
    }
    this.hide();
  }

  private hideAfter(ms: number): void {
    if (this.pillTimer) clearTimeout(this.pillTimer);
    this.pillTimer = setTimeout(() => {
      this.pillTimer = null;
      this.hide();
    }, ms);
  }

  hide(): void {
    if (!this.layer?.isConnected) return;
    this.pill.classList.remove('on', 'low');
    this.frame.classList.remove('on', 'wait', 'look');
    this.keys.classList.remove('on');
  }

  /** The capsule moves out of the way of a tap that would land under it. */
  private dodge(x: number, y: number): void {
    const r = this.pill.getBoundingClientRect();
    const root = this.layer!.getBoundingClientRect();
    const scale = root.width > 0 ? root.width / PHONE_WIDTH : 1;
    const px = root.left + x * scale;
    const py = root.top + y * scale;
    const margin = 14 * scale;
    const under = px > r.left - margin && px < r.right + margin && py > r.top - margin && py < r.bottom + margin;
    if (!under) return;
    this.pill.classList.toggle('low');
    if (this.lowTimer) clearTimeout(this.lowTimer);
    this.lowTimer = setTimeout(() => {
      if (this.pill.classList.contains('low') && this.state === 'working') this.pill.classList.remove('low');
    }, 4000);
  }

  private showKeys(html: string, forMs: number): void {
    this.keys.innerHTML = html;
    this.keys.classList.remove('on');
    void this.keys.offsetWidth;
    this.keys.classList.add('on');
    if (this.keysTimer) clearTimeout(this.keysTimer);
    this.keysTimer = setTimeout(() => this.keys.classList.remove('on'), forMs);
  }

  // ------------------------------------------------------------------ the marks
  private addMark(m: Omit<Mark, 'born' | 'life'> & { life?: number }): void {
    this.ensure();
    this.marks.push({ ...m, born: performance.now(), life: m.life ?? RING_MS });
    if (!this.drawing) {
      this.drawing = true;
      requestAnimationFrame((now) => this.draw(now));
    }
  }

  private draw(now: number): void {
    const ctx = this.ctx;
    this.marks = this.marks.filter((m) => now - m.born < m.life);
    ctx.clearRect(0, 0, PHONE_WIDTH, PHONE_HEIGHT);
    if (!this.marks.length) {
      this.drawing = false;
      return;
    }
    for (const m of this.marks) {
      const tt = (now - m.born) / m.life;
      switch (m.kind) {
        case 'trail':
          this.drawTrail(m, tt);
          break;
        case 'drag':
          this.drawDrag(m, tt, now);
          break;
        case 'hold':
          this.drawPoint(m, Math.min(tt * 1.3, 1), now);
          this.drawHold(m, tt);
          break;
        default:
          this.drawPoint(m, tt, now);
      }
      if (m.label && m.kind !== 'trail') this.drawLabel(m, tt);
    }
    requestAnimationFrame((n) => this.draw(n));
  }

  private drawPoint(m: Mark, tt: number, now: number): void {
    const ctx = this.ctx;
    const halo = ctx.createRadialGradient(m.x, m.y, 3, m.x, m.y, 30);
    halo.addColorStop(0, `rgba(10,102,228,${0.22 * (1 - tt)})`);
    halo.addColorStop(1, 'rgba(10,102,228,0)');
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(m.x, m.y, 30, 0, Math.PI * 2);
    ctx.fill();
    for (const delay of [0, 0.16]) {
      const r = clamp01((tt - delay) / (1 - delay));
      if (r <= 0) continue;
      ctx.beginPath();
      ctx.arc(m.x, m.y, 15 + easeOut(r) * 50, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(10,102,228,${(1 - r) * 0.5})`;
      ctx.lineWidth = 2.2 - r * 1.4;
      ctx.stroke();
    }
    const a = Math.min(1, 1.6 - tt);
    ctx.beginPath();
    ctx.arc(m.x, m.y, 17, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(10,102,228,${a * 0.22})`;
    ctx.lineWidth = 8;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(m.x, m.y, 17, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(10,102,228,${a})`;
    ctx.lineWidth = 2.5;
    ctx.stroke();
    const from = (now / 520) % (Math.PI * 2);
    ctx.beginPath();
    ctx.arc(m.x, m.y, 23, from, from + Math.PI * 0.75);
    ctx.strokeStyle = `rgba(6,182,212,${a * 0.95})`;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(m.x, m.y, 3.8, 0, Math.PI * 2);
    ctx.fillStyle = ACCENT;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(m.x, m.y, 1.5, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
  }

  /** A long press: a second ring filling for as long as the finger is held. */
  private drawHold(m: Mark, tt: number): void {
    const ctx = this.ctx;
    const fill = clamp01(tt * (m.life / Math.max(1, m.life - 500)));
    ctx.beginPath();
    ctx.arc(m.x, m.y, 28, -Math.PI / 2, -Math.PI / 2 + fill * Math.PI * 2);
    ctx.strokeStyle = `rgba(6,182,212,${0.9 * (1 - Math.max(0, (tt - 0.8) / 0.2))})`;
    ctx.lineWidth = 3;
    ctx.lineCap = 'round';
    ctx.stroke();
  }

  private drawTrail(m: Mark, tt: number): void {
    if (typeof m.x2 !== 'number' || typeof m.y2 !== 'number') return;
    const ctx = this.ctx;
    const p = easeOut(tt);
    const hx = m.x + (m.x2 - m.x) * p;
    const hy = m.y + (m.y2 - m.y) * p;
    const grad = ctx.createLinearGradient(m.x, m.y, hx, hy);
    grad.addColorStop(0, 'rgba(6,182,212,0)');
    grad.addColorStop(1, `rgba(6,182,212,${0.8 * (1 - tt)})`);
    ctx.beginPath();
    ctx.moveTo(m.x, m.y);
    ctx.lineTo(hx, hy);
    ctx.strokeStyle = grad;
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.stroke();
    if (tt < 0.95) {
      ctx.beginPath();
      ctx.arc(hx, hy, 3, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(6,182,212,${1 - tt})`;
      ctx.fill();
    }
  }

  private drawDrag(m: Mark, tt: number, now: number): void {
    this.drawPoint(m, Math.min(tt * 1.4, 1), now);
    if (typeof m.x2 !== 'number' || typeof m.y2 !== 'number') return;
    const ctx = this.ctx;
    const p = easeOut(clamp01(tt * 1.5));
    const ex = m.x + (m.x2 - m.x) * p;
    const ey = m.y + (m.y2 - m.y) * p;
    const fade = 1 - Math.max(0, (tt - 0.7) / 0.3);
    const grad = ctx.createLinearGradient(m.x, m.y, m.x2, m.y2);
    grad.addColorStop(0, `rgba(10,102,228,${0.9 * fade})`);
    grad.addColorStop(1, `rgba(6,182,212,${0.9 * fade})`);
    ctx.beginPath();
    ctx.moveTo(m.x, m.y);
    ctx.lineTo(ex, ey);
    ctx.strokeStyle = grad;
    ctx.lineWidth = 3;
    ctx.setLineDash([9, 6]);
    ctx.lineDashOffset = -now / 40;
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(ex, ey, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(6,182,212,${fade})`;
    ctx.fill();
    if (p >= 1) {
      const ang = Math.atan2(m.y2 - m.y, m.x2 - m.x);
      ctx.beginPath();
      ctx.moveTo(m.x2, m.y2);
      ctx.lineTo(m.x2 - 11 * Math.cos(ang - 0.45), m.y2 - 11 * Math.sin(ang - 0.45));
      ctx.lineTo(m.x2 - 11 * Math.cos(ang + 0.45), m.y2 - 11 * Math.sin(ang + 0.45));
      ctx.closePath();
      ctx.fillStyle = `rgba(6,182,212,${fade})`;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(m.x2, m.y2, 15, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(6,182,212,${0.8 * fade})`;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }

  private drawLabel(m: Mark, tt: number): void {
    if (tt >= 0.85) return;
    const ctx = this.ctx;
    const text = m.label.length > 22 ? `${m.label.slice(0, 22)}…` : m.label;
    ctx.font = FONT;
    const w = ctx.measureText(text).width + 22;
    const h = 24;
    let lx = m.x + 28;
    let ly = m.y - 32;
    if (lx + w > PHONE_WIDTH - 6) lx = Math.max(6, m.x - 28 - w);
    if (ly < STATUS_BAR + 4) ly = m.y + 28;
    const pop = Math.min(1, tt / 0.09);
    const scale = 0.92 + 0.08 * easeOut(pop);
    ctx.save();
    ctx.globalAlpha = Math.min(pop, 1 - Math.max(0, (tt - 0.62) / 0.23));
    ctx.translate(lx + w / 2, ly + h / 2);
    ctx.scale(scale, scale);
    ctx.translate(-(lx + w / 2), -(ly + h / 2));
    ctx.fillStyle = 'rgba(0,0,0,.1)';
    this.roundRect(lx - 2, ly + 2, w + 4, h + 4, 14);
    ctx.fill();
    ctx.fillStyle = 'rgba(17,18,24,.88)';
    this.roundRect(lx, ly, w, h, 12);
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.1)';
    ctx.lineWidth = 1;
    this.roundRect(lx + 0.5, ly + 0.5, w - 1, h - 1, 11.5);
    ctx.stroke();
    const bar = ctx.createLinearGradient(0, ly, 0, ly + h);
    bar.addColorStop(0, CYAN);
    bar.addColorStop(1, ACCENT);
    ctx.fillStyle = bar;
    this.roundRect(lx + 8, ly + 7, 2.5, h - 14, 1.25);
    ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, lx + 16, ly + h / 2 + 0.5);
    ctx.restore();
  }

  private roundRect(x: number, y: number, w: number, h: number, r: number): void {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
}

export const stage = new Stage();

/** Whether the phone's language is Chinese right now (for the words in captions). */
export function zh(): boolean {
  return getLocale() === 'zh-Hans';
}
