import { createAppStoreWithActions } from '@/os/createAppStore';
import { NANOMUSE_CONFIG } from './data';
import { bridge } from './bridge';
import { installHost } from './host';

export type LinkState = 'off' | 'connecting' | 'online' | 'unauthorized' | 'unreachable';

/** A session on the hosted showcase (see `demo.ts`); `null` when connected to your own server. */
export interface DemoRecord {
  id: string;
  /** Unix seconds. */
  expiresAt: number;
  byok: boolean;
}

interface NanoMuseState {
  /** Origin of the NanoLika server, no trailing slash: "http://127.0.0.1:8787". */
  serverUrl: string;
  /** The access token `nanomuse serve` prints (also in the QR code). */
  token: string;
  /** Mirror approvals, questions and background results into the notification shade. */
  notify: boolean;
  /** Let NanoLika operate this phone through its screen (its own GUI switch must be on too). */
  gui: boolean;
  /** The hosted showcase session this phone is on, if any. */
  demo: DemoRecord | null;
  /** The showcase's ticket from a NanoLika Cloud sign-in (demo.ts), and who it is for. */
  ticket: string;
  visitor: { hint: string; channel: string } | null;
  /** Live state of the notification bridge's WebSocket. Not persisted. */
  link: LinkState;
  /** The colour scheme the web app in the frame reports (light until it says otherwise). Not persisted. */
  webTheme: 'light' | 'dark';
}

interface NanoMuseActions {
  configure: (serverUrl: string, token: string, demo?: DemoRecord | null) => void;
  disconnect: () => void;
  setNotify: (on: boolean) => void;
  setGui: (on: boolean) => void;
  setLink: (link: LinkState) => void;
  setWebTheme: (theme: 'light' | 'dark') => void;
  setVisitor: (ticket: string, visitor: { hint: string; channel: string } | null) => void;
}

const initialState: NanoMuseState = {
  serverUrl: NANOMUSE_CONFIG.serverUrl,
  token: NANOMUSE_CONFIG.token,
  notify: NANOMUSE_CONFIG.notify,
  gui: NANOMUSE_CONFIG.gui,
  demo: null,
  ticket: '',
  visitor: null,
  link: 'off',
  webTheme: 'light',
};

/** Normalise what people paste: a bare host, an origin, or the full link the runtime
 *  printed — `#token=…` (the fragment never reaches a server) or the older `?token=…`. */
export function parseServerInput(raw: string): { serverUrl: string; token: string } {
  let text = raw.trim();
  if (!text) return { serverUrl: '', token: '' };
  if (!/^[a-z]+:\/\//i.test(text)) text = `http://${text}`;
  try {
    const url = new URL(text);
    const fragment = /(?:^#|&)token=([^&]+)/.exec(url.hash);
    const token = fragment ? decodeURIComponent(fragment[1]) : (url.searchParams.get('token') ?? '');
    return { serverUrl: `${url.protocol}//${url.host}`, token };
  } catch {
    return { serverUrl: text.replace(/\/+$/, ''), token: '' };
  }
}

export const useNanoMuseStore = createAppStoreWithActions<NanoMuseState, NanoMuseActions>(
  'nanomuse',
  initialState,
  (set) => ({
    configure(serverUrl, token, demo = null) {
      set({ serverUrl: serverUrl.replace(/\/+$/, ''), token, demo, link: 'connecting' });
    },
    disconnect() {
      set({ serverUrl: '', token: '', demo: null, link: 'off' });
    },
    setNotify(on) {
      set({ notify: on });
    },
    setGui(on) {
      set({ gui: on });
    },
    setLink(link) {
      set({ link });
    },
    setWebTheme(webTheme) {
      set({ webTheme });
    },
    setVisitor(ticket, visitor) {
      set({ ticket, visitor: ticket ? visitor : null });
    },
  }),
  {
    // `link` and `webTheme` are runtime state: the bridge and the web app set them
    partialize: (s) => ({
      serverUrl: s.serverUrl,
      token: s.token,
      notify: s.notify,
      gui: s.gui,
      demo: s.demo,
      ticket: s.ticket,
      visitor: s.visitor,
    }),
    afterHydration: () => bridge.sync(),
  },
);

// Keep the bridge in step with the server settings for as long as the simulator runs, whether
// or not the app is open — that is what makes notifications arrive while you are in another app.
bridge.attach({
  get: () => {
    const { serverUrl, token, notify, gui } = useNanoMuseStore.getState();
    return { serverUrl, token, notify, gui };
  },
  setLink: (link) => useNanoMuseStore.getState().setLink(link),
});
// hydration from localStorage is synchronous, so `afterHydration` above may already have run
// before the bridge had its hooks; this call is a no-op when it did connect
bridge.sync();
useNanoMuseStore.subscribe((s, prev) => {
  if (s.serverUrl !== prev.serverUrl || s.token !== prev.token || s.notify !== prev.notify || s.gui !== prev.gui) bridge.sync();
});
// the page around the phone (the showcase site) reaches this app through window.__NANOMUSE__
installHost();
