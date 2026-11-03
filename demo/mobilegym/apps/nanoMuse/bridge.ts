import { NotificationService } from '@/os/NotificationService';
import { realNow } from '@/os/TimeService';
import { SCREEN, act, installedApps, readScreen, type ActParams } from './gui';
import { manifest } from './manifest';
import { fmt, t } from './res/strings';
import { stage } from './stage';

/**
 * The notification bridge.
 *
 * nanoMuse pushes real Web Push notifications to real phones. Inside the simulator there is
 * no push service, so this module keeps one WebSocket to the nanoMuse server open for the
 * whole simulator session and turns the events a phone would be notified about into
 * simulated Android notifications: approvals waiting for you, questions the agent asked,
 * and results of background work. Tapping one opens the nanoMuse app on that chat.
 *
 * The same socket makes this simulated phone a device the agent can operate (see `gui.ts`):
 * after `hello` the module announces itself with `{"kind": "device", "gui": true, "capsule":
 * true}` and answers the server's `device_request` messages — its screen as a picture, one
 * action by coordinates, or a `task` event (`begin` / `end` / `notice`) for the capsule — with
 * `device_result`. **Stop** on the capsule fails the request under way, and every one after
 * it until the next task, with the `nanomuse:stop` marker the server reads as "the person
 * stopped it on the phone" — the Android app's contract (docs/gui.md).
 *
 * While the hands work, the person stays in the operated app: an `approval` the run raises
 * is shown on the capsule with **Allow once / Deny** and answered from here
 * (`POST /api/approvals/{id}`, the token in the Authorization header), never by sending the
 * phone back into nanoMuse; a `hold` (the agent handed the phone over, or the person took it
 * from the chat) is a card with **Done** (`POST /api/holds/{id}/done`). Only a question a
 * finished task left offers *Open*.
 *
 * When a task is over — its `end`, or Stop — the phone comes back to nanoMuse, where the
 * agent's report is, the way the Android app brings itself to the front after its hands are
 * done. The capsule does not depend on the `end` arriving: the server's `hello` carries the
 * task under way (`state.phone.task`) and the open holds (`state.holds`), so a socket that
 * reconnects sets the capsule from that — a task still on gets its capsule back, a task that
 * ended meanwhile takes it down — and a socket that closes for good takes it down at once.
 */

type LinkState = 'off' | 'connecting' | 'online' | 'unauthorized' | 'unreachable';

interface Settings {
  serverUrl: string;
  token: string;
  notify: boolean;
  /** Let the agent operate this phone (read the screen, tap, type) when its GUI switch is on. */
  gui: boolean;
}

interface Hooks {
  get: () => Settings;
  setLink: (link: LinkState) => void;
}

/** The subset of the server's timeline events we care about. */
interface TimelineEvent {
  id: string;
  type: string;
  thread: string;
  status?: string;
  summary?: string;
  purpose?: string;
  text?: string;
  source?: string;
  about?: string;
  quiet?: boolean;
  final?: boolean;
  tool?: string;
  /** `hold` events: who paused the hands, and why. */
  by?: string;
  reason?: string;
}

/** The server's word on the phone in its `hello`: `task` is the one under way, `null` when none
 * (an older server has no `task` key at all — then it is not known). */
interface PhoneState {
  task?: { goal?: string; since?: number } | null;
}

type WsMessage =
  | { kind: 'hello'; state: { pending_approvals?: TimelineEvent[]; holds?: TimelineEvent[]; phone?: PhoneState } }
  | { kind: 'event' | 'update'; event: TimelineEvent }
  | { kind: 'device_request'; id: string; op: string; params?: Record<string, unknown> }
  | { kind: string };

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 30000;
/** A socket lost under a task: this long for it to come back before the capsule comes down. */
const LINK_LOST_MS = 15000;
/** The tick (or the cross) is seen this long before the phone comes back to nanoMuse. */
const COME_BACK_MS = 1300;

function threadRoute(thread: string): string {
  return `/?thread=${encodeURIComponent(thread)}`;
}

/** MobileGym's runtime API, as far as this module needs it (the simulator owns its types). */
interface SimOs {
  openApp?: (id: string, path?: string) => void;
  launchApp?: (id: string) => void;
  getState?: () => { activeAppId?: string | null } | null;
}

function simOs(): SimOs | undefined {
  return (window as unknown as { __OS__?: SimOs }).__OS__;
}

/** The agent's name as the user set it; learnt from the server's hello and profile updates. */
let agentName = 'nanoMuse';

/** Same titles the server uses for its Web Push notifications, in the phone's language. */
function backgroundTitle(about: string): string {
  const s = t();
  if (about.startsWith('Check-in: ')) return fmt(s.notify_check_in, agentName);
  if (about.startsWith('Reminder: ')) return fmt(s.notify_reminder, agentName);
  if (about.startsWith('Routine: ')) return about.slice('Routine: '.length) || agentName;
  if (about.startsWith('New mail: ')) return fmt(s.notify_new_mail, agentName);
  if (about.startsWith('Coming up: ')) return fmt(s.notify_coming_up, agentName);
  if (about.startsWith('Webhook: ')) return fmt(s.notify_webhook, agentName);
  return about.replace('Working on your goal: ', '') || agentName;
}

/** The marker the server reads as "stopped by the person on the device" (nanomuse.phone.link). */
const STOP_MARKER = 'nanomuse:stop';

/** The face the capsule shows: the dragon, or the studio's face on the server — a link signed
 *  with the token rather than carrying it (the runtime's nanomuse/server/tickets.py: a logged
 *  link opens that one file for a few hours, nothing else). */
async function faceUrl(serverUrl: string, token: string, avatar: string | undefined): Promise<string> {
  if (!serverUrl) return '';
  if (!avatar || avatar === 'dragon') return `${serverUrl}/avatars/lika-working.webp`;
  const path = `/api/files/avatar/${avatar}/working.webp`;
  const exp = (Math.floor(realNow() / 1000 / 21600) + 2) * 21600;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(token), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(`${exp}\n${path}`)));
  const sig = Array.from(mac, (b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
  return `${serverUrl}/api/files/avatar/${encodeURIComponent(avatar)}/working.webp?exp=${exp}&sig=${sig}`;
}

function setIdentity(serverUrl: string, token: string, profile: { name?: string; avatar?: string } | undefined): void {
  if (profile?.name) agentName = profile.name;
  void faceUrl(serverUrl, token, profile?.avatar).then((url) => showFace(serverUrl, url));
}

function showFace(serverUrl: string, url: string): void {
  if (!url) return;
  const fallback = `${serverUrl}/avatars/lika-working.webp`;
  if (url === fallback) {
    stage.setIdentity(agentName, url);
    return;
  }
  // a studio face that cannot be loaded (gone, or not drawn yet) leaves the dragon
  const probe = new Image();
  probe.onload = () => stage.setIdentity(agentName, url);
  probe.onerror = () => stage.setIdentity(agentName, fallback);
  probe.src = url;
  stage.setIdentity(agentName, fallback);
}

function firstLine(text: string, max = 140): string {
  const line = text.replace(/\s+/g, ' ').trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

class MuseBridge {
  private hooks: Hooks | null = null;
  private ws: WebSocket | null = null;
  private key = '';
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private backoff = RECONNECT_MIN_MS;
  /** timeline event id → notification id, so a resolved card takes its notification down */
  private shown = new Map<string, string>();
  /** Stop was tapped on the capsule: every request fails until the next task begins. */
  private stopped = false;
  private stopWaiters: Array<(err: Error) => void> = [];
  /** the chat the last approval or question came from — where Open on the capsule goes */
  private lastThread = 'main';
  private lostTimer: ReturnType<typeof setTimeout> | null = null;
  private backTimer: ReturnType<typeof setTimeout> | null = null;
  /** Acts the agent made outside a task (`phone_act` straight from the chat: open an app, read
   * it) since nanoMuse last came back; they too can leave another app in front. */
  private loneActs = 0;
  /** The approval on the capsule, so a failed answer can put the card back as it was. */
  private approvalShown: { id: string; summary: string; purpose: string } | null = null;
  /** The phone's open holds, by id (contract C1), to tell a hold that ended from the rest. */
  private holds = new Map<string, TimelineEvent>();

  constructor() {
    stage.onStop = () => this.stop();
    stage.onOpen = () => this.openApp(this.lastThread);
    stage.onGone = () => this.comeBack();
    stage.onAllow = (id) => void this.decide(id, true);
    stage.onDeny = (id) => void this.decide(id, false);
    stage.onDone = (id) => void this.holdDone(id);
  }

  /** One call to the server's API with the token the socket signed in with. */
  private async api(path: string, body: Record<string, unknown>): Promise<boolean> {
    const settings = this.hooks?.get();
    if (!settings?.serverUrl || !settings.token) return false;
    try {
      const r = await fetch(`${settings.serverUrl}${path}`, {
        method: 'POST',
        headers: { authorization: `Bearer ${settings.token}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) console.warn(`[nanoMuse] ${path} → ${r.status}`);
      return r.ok;
    } catch (err) {
      console.warn(`[nanoMuse] ${path} failed`, err);
      return false;
    }
  }

  /**
   * Allow once / Deny on the capsule: the same answer the card in the chat gives, for this
   * one time (`scope: "once"`; a standing grant is only given from the card, where the object
   * is spelled out). The server's `update` of the event takes the card down; a call that did
   * not get through puts it back with a word.
   */
  private async decide(id: string, approved: boolean): Promise<void> {
    const shown = this.approvalShown;
    const ok = await this.api(`/api/approvals/${encodeURIComponent(id)}`, {
      approved,
      scope: 'once',
      reason: approved ? 'allowed on the phone' : 'denied on the phone',
    });
    if (ok) {
      this.retract(id);
      // a 404 already answered elsewhere comes as an update too; this is for the normal case
      stage.resume(id);
      if (this.approvalShown?.id === id) this.approvalShown = null;
    } else if (shown && shown.id === id) {
      stage.approvalFailed(id, shown.summary, shown.purpose);
    }
  }

  /** Done on a hold card: the person is finished; the hands take a fresh look and go on. */
  private async holdDone(id: string): Promise<void> {
    const ev = this.holds.get(id);
    const ok = await this.api(`/api/holds/${encodeURIComponent(id)}/done`, {});
    if (!ok && ev && this.holds.has(id)) {
      // the server did not hear: the card comes back, Done can be tapped again
      stage.hold(id, ev.by ?? 'agent', ev.reason ?? '');
    }
  }

  /** A `hold` event for this phone: on → the card; off → back to work. Other tools' holds are not ours. */
  private hold(ev: TimelineEvent): void {
    if (ev.tool && ev.tool !== 'phone') return;
    if (ev.thread) this.lastThread = ev.thread;
    if (ev.status === 'on') {
      this.holds.set(ev.id, ev);
      if (this.hooks?.get().gui) stage.hold(ev.id, ev.by ?? 'agent', ev.reason ?? '');
    } else {
      this.holds.delete(ev.id);
      stage.holdOff(ev.id);
    }
  }

  /** The server's `hello` lists the holds still open: the capsule shows ours, forgets the rest. */
  private syncHolds(open: TimelineEvent[] | undefined): void {
    if (!open) return; // an older server: nothing is known, nothing is assumed
    const ours = open.filter((h) => h.type === 'hold' && (!h.tool || h.tool === 'phone') && h.status !== 'off');
    const ids = new Set(ours.map((h) => h.id));
    for (const id of Array.from(this.holds.keys())) {
      if (!ids.has(id)) {
        this.holds.delete(id);
        stage.holdOff(id);
      }
    }
    for (const h of ours) this.hold({ ...h, status: 'on' });
  }

  /** An `approval` event while the hands work: the card on the capsule, decided here. */
  private approval(ev: TimelineEvent): void {
    if (ev.status === 'pending') {
      this.approvalShown = { id: ev.id, summary: ev.summary ?? '', purpose: ev.purpose ?? '' };
      stage.approval(ev.id, ev.summary ?? '', ev.purpose ?? '');
    } else {
      if (this.approvalShown?.id === ev.id) this.approvalShown = null;
      stage.resume(ev.id);
    }
  }

  /** The task is over without a word from the server: the capsule comes down, and if the
   * hands went anywhere, the phone comes back to nanoMuse — the report is there. */
  private gone(): void {
    if (!stage.active) return;
    const wentSomewhere = stage.steps > 0;
    stage.reset();
    if (wentSomewhere) this.comeBack();
  }

  /** Stop on the capsule: fail what is under way, and what comes after, until a new task. */
  private stop(): void {
    this.stopped = true;
    const err = new Error(`${STOP_MARKER}: stopped by the person on the phone`);
    const waiters = this.stopWaiters;
    this.stopWaiters = [];
    for (const w of waiters) w(err);
    const wasOn = stage.active;
    stage.stopped();
    // as on Android: the hands are off the phone, the chat (which asks what to do next) comes up
    if (wasOn) this.comeBack();
  }

  /** Bring nanoMuse to the front, on a chat. */
  private openApp(thread: string): void {
    try {
      const os = simOs();
      if (os?.openApp) os.openApp(manifest.id, threadRoute(thread));
      else os?.launchApp?.(manifest.id);
    } catch {
      // the launcher is not up yet
    }
  }

  /**
   * After the capsule's last moment, nanoMuse to the front as it was — the chat the task was
   * given in, with the agent's report arriving — unless it is there already. What the Android
   * app's `HandsCapsule.bringAppToFront` does once its hands are done.
   */
  private comeBack(): void {
    this.loneActs = 0;
    if (this.backTimer) clearTimeout(this.backTimer);
    this.backTimer = setTimeout(() => {
      this.backTimer = null;
      try {
        const os = simOs();
        if (os?.getState?.()?.activeAppId === manifest.id) return;
        os?.launchApp?.(manifest.id);
      } catch {
        // the launcher is not up
      }
    }, COME_BACK_MS);
  }

  attach(hooks: Hooks): void {
    this.hooks = hooks;
  }

  /** (Re)connect if the settings changed; disconnect if they were cleared. Idempotent. */
  sync(): void {
    if (!this.hooks) return;
    const { serverUrl, token, notify, gui } = this.hooks.get();
    // the socket is needed for notifications or for GUI operation; either keeps it open
    const key = serverUrl && token && (notify || gui) ? `${serverUrl}|${token}|${gui ? 'g' : ''}` : '';
    if (key === this.key && (this.ws || this.reconnectTimer)) return;
    this.key = key;
    this.teardown();
    if (!key) {
      this.hooks.setLink('off');
      return;
    }
    this.backoff = RECONNECT_MIN_MS;
    this.open();
  }

  private teardown(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    // the settings changed under a task: whatever was on, this phone is no longer in it
    this.linkLost(true);
    if (this.ws) {
      const ws = this.ws;
      this.ws = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
  }

  private open(): void {
    if (!this.hooks) return;
    const { serverUrl, token } = this.hooks.get();
    let url: URL;
    try {
      url = new URL(serverUrl);
    } catch {
      this.hooks.setLink('unreachable');
      return;
    }
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = '/ws';
    url.search = '';

    this.hooks.setLink('connecting');
    const ws = new WebSocket(url.toString());
    this.ws = ws;
    const settingsKey = this.key;

    ws.onopen = () => {
      // the token goes in the first frame, never in the address (which the gateway's proxy logs)
      ws.send(JSON.stringify({ kind: 'auth', token }));
      this.backoff = RECONNECT_MIN_MS;
    };
    ws.onmessage = (ev) => {
      let msg: WsMessage;
      try {
        msg = JSON.parse(String(ev.data)) as WsMessage;
      } catch {
        return;
      }
      this.handle(msg);
    };
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (!this.hooks || this.key !== settingsKey) return;
      if (ev.code === 4401 || ev.code === 4404) {
        // the server rejected the token (4401), or the showcase gateway says this session is
        // gone (4404): no point retrying until the settings change
        this.hooks.setLink('unauthorized');
        this.linkLost(true);
        return;
      }
      this.hooks.setLink('unreachable');
      this.linkLost(false);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        if (this.key === settingsKey) this.open();
      }, this.backoff);
      this.backoff = Math.min(this.backoff * 2, RECONNECT_MAX_MS);
    };
    ws.onerror = () => {
      // onclose follows and does the bookkeeping
    };
  }

  private send(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  /** `{"kind": "device", ...}`: from now on the server may ask this phone for its screen. */
  private announce(): void {
    if (!this.hooks?.get().gui) return;
    this.send({
      kind: 'device',
      name: 'MobileGym',
      platform: 'mobilegym',
      gui: true,
      capsule: true,
      apps: installedApps(),
      screen: { width: SCREEN.width, height: SCREEN.height },
    });
  }

  /** A `task` request: the capsule follows the task. */
  private task(params: Record<string, unknown>): void {
    const event = String(params.event ?? '');
    const text = String(params.text ?? '');
    if (event === 'begin') {
      this.stopped = false;
      stage.begin(text);
    } else if (event === 'end') {
      // over: the tick, then back to nanoMuse — if the hands went anywhere (a task that
      // ended before its first action left the phone where it was)
      const wentSomewhere = stage.active && stage.steps > 0;
      stage.end();
      if (wentSomewhere) this.comeBack();
    } else if (event === 'notice') {
      stage.notice(text);
    }
  }

  /**
   * The server's `hello` says whether a task is on. The capsule follows that rather than the
   * `end` alone: a socket that reconnects in the middle of a task gets its capsule back, one
   * that reconnects after the task ended (its `end` lost with the old socket) takes it down.
   * An older server says nothing (`task` missing): then nothing is assumed.
   */
  private syncTask(phone: PhoneState | undefined): void {
    if (this.lostTimer) {
      clearTimeout(this.lostTimer);
      this.lostTimer = null;
    }
    if (!phone || phone.task === undefined) return;
    if (phone.task) {
      if (!stage.active && this.hooks?.get().gui) {
        this.stopped = false;
        stage.begin(String(phone.task.goal ?? ''));
      }
    } else {
      this.gone();
    }
  }

  /**
   * The socket closed under a task. For good (the token refused, the session gone, the
   * settings changed): the capsule comes down now. Otherwise the reconnect's `hello` sets it
   * right — and if none comes within LINK_LOST_MS, it comes down anyway.
   */
  private linkLost(forGood: boolean): void {
    if (!stage.active) return;
    if (forGood) {
      this.gone();
      return;
    }
    if (this.lostTimer) return; // the clock runs from the first loss, not the last retry
    this.lostTimer = setTimeout(() => {
      this.lostTimer = null;
      if (!this.ws) this.gone();
    }, LINK_LOST_MS);
  }

  private async serve(msg: { id: string; op: string; params?: Record<string, unknown> }): Promise<void> {
    if (!this.hooks?.get().gui) {
      this.send({ kind: 'device_result', id: msg.id, ok: false, error: 'GUI operation is turned off on this phone' });
      return;
    }
    const params = msg.params ?? {};
    if (msg.op === 'task') {
      this.task(params);
      this.send({ kind: 'device_result', id: msg.id, ok: true, result: {} });
      return;
    }
    if (this.stopped) {
      this.send({ kind: 'device_result', id: msg.id, ok: false, error: `${STOP_MARKER}: stopped by the person on the phone` });
      return;
    }
    // Stop on the capsule fails the request under way at once, whatever the gesture is doing
    let halt: ((err: Error) => void) | null = null;
    const halted = new Promise<never>((_, reject) => {
      halt = reject;
      this.stopWaiters.push(reject);
    });
    try {
      let work: Promise<unknown>;
      if (msg.op === 'screen') work = readScreen();
      else if (msg.op === 'act') work = act(params as unknown as ActParams);
      else throw new Error(`unknown op '${msg.op}'`);
      const result = await Promise.race([work, halted]);
      if (msg.op === 'act' && !stage.active) this.loneActs += 1;
      this.send({ kind: 'device_result', id: msg.id, ok: true, result });
    } catch (err) {
      this.send({ kind: 'device_result', id: msg.id, ok: false, error: err instanceof Error ? err.message : String(err) });
    } finally {
      this.stopWaiters = this.stopWaiters.filter((w) => w !== halt);
    }
  }

  private handle(msg: WsMessage): void {
    if (!this.hooks) return;
    if (msg.kind === 'hello') {
      this.hooks.setLink('online');
      this.announce();
      const { serverUrl, token } = this.hooks.get();
      setIdentity(serverUrl, token, (msg as { state: { profile?: { name?: string; avatar?: string } } }).state.profile);
      this.syncTask((msg as { state: { phone?: PhoneState } }).state.phone);
      this.syncHolds((msg as { state: { holds?: TimelineEvent[] } }).state.holds);
      const pending = (msg as { state: { pending_approvals?: TimelineEvent[] } }).state.pending_approvals ?? [];
      // a yes still waited for while the hands are on this phone: the capsule asks, not the shade
      const onCapsule = stage.active ? pending.find((ev) => ev.type === 'approval' && ev.status === 'pending') : undefined;
      if (onCapsule) this.approval(onCapsule);
      if (!this.hooks.get().notify) return;
      for (const ev of pending) if (ev !== onCapsule) this.notifyFor(ev);
      return;
    }
    if (msg.kind === 'device_request') {
      void this.serve(msg as { id: string; op: string; params?: Record<string, unknown> });
      return;
    }
    if (msg.kind === 'profile') {
      const { serverUrl, token } = this.hooks.get();
      setIdentity(serverUrl, token, (msg as { profile?: { name?: string; avatar?: string } }).profile);
      return;
    }
    if (msg.kind === 'event' || msg.kind === 'update') {
      const ev = (msg as { event: TimelineEvent }).event;
      if (ev.type === 'hold') {
        this.hold(ev);
        return;
      }
      if (ev.type === 'approval' || ev.type === 'question') {
        if (ev.thread) this.lastThread = ev.thread;
        // while the hands work, the capsule carries the approval — Allow once / Deny, decided
        // here — so the person is not sent back into nanoMuse for it
        if (ev.type === 'approval' && (stage.active || this.approvalShown?.id === ev.id)) {
          this.approval(ev);
          if (ev.status === 'pending') return; // the capsule asks; no notification on top of it
          this.retract(ev.id);
          return;
        }
      } else if (ev.type === 'assistant' && ev.final && ev.source !== 'background') {
        if (stage.active) {
          // The chat run that was using the hands has said its last word, so the task is over
          // whatever became of its `end` (a socket that blinked at that very moment, say). The
          // capsule must not stay on "step 6, looking" over a conversation that has finished.
          const wentSomewhere = stage.steps > 0;
          stage.end();
          if (wentSomewhere) this.comeBack();
        } else if (this.loneActs > 0) {
          // No task, but the agent opened or worked an app straight from the chat and has now
          // answered: the answer is in nanoMuse, so nanoMuse comes to the front for it.
          this.comeBack();
        }
      }
    }
    if (!this.hooks.get().notify) return;
    if (msg.kind === 'event' || msg.kind === 'update') {
      const ev = (msg as { event: TimelineEvent }).event;
      if (ev.type === 'approval' || ev.type === 'question') {
        if (ev.status === 'pending') this.notifyFor(ev);
        else this.retract(ev.id);
        return;
      }
      if (ev.type === 'assistant') {
        // Background work reporting in. Only the run's last word is flagged `final` (the
        // step-by-step narration before it is not), and the agent already decided it was
        // worth surfacing (`quiet` otherwise). The flag can arrive as an update when the
        // bubble was on screen before the run ended, hence the dedupe.
        if (ev.source === 'background' && ev.final && !ev.quiet && ev.text && !this.shown.has(ev.id)) {
          const item = NotificationService.push({
            appId: manifest.id,
            title: backgroundTitle(ev.about ?? ''),
            body: firstLine(ev.text),
            route: threadRoute(ev.thread),
            importance: 'default',
          });
          this.shown.set(ev.id, item.id);
        }
      }
    }
  }

  private notifyFor(ev: TimelineEvent): void {
    if (this.shown.has(ev.id)) return;
    const isApproval = ev.type === 'approval';
    const s = t();
    const body = isApproval ? [ev.summary, ev.purpose && fmt(s.notify_for, ev.purpose)].filter(Boolean).join(' — ') : ev.text ?? '';
    const item = NotificationService.push({
      appId: manifest.id,
      title: isApproval ? fmt(s.notify_approval, agentName) : fmt(s.notify_question, agentName),
      body: firstLine(body, 200),
      route: threadRoute(ev.thread),
      importance: 'high',
      autoCancel: true,
    });
    this.shown.set(ev.id, item.id);
  }

  private retract(eventId: string): void {
    const id = this.shown.get(eventId);
    if (!id) return;
    this.shown.delete(eventId);
    NotificationService.dismiss(id);
  }
}

export const bridge = new MuseBridge();
