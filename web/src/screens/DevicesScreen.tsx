import { Check, Globe, Hand, Laptop, MessageSquare, MoreHorizontal, Pencil, RefreshCw, Smartphone, Square, Wifi, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { PageBar } from "../components/BackBar";
import { CloudCard } from "../components/CloudCard";
import { MuseCaption, MuseCard, MuseDivider, MuseRow, MuseSwitchRow } from "../components/MuseList";
import { useMenuKeys } from "../components/useMenuKeys";
import { useT, type t as T } from "../i18n";
import { useStore } from "../store";
import type { CloudSession, HandsStatus, HubDevice, HubView } from "../types";
import { cx, relativeTime } from "../util";

/**
 * Devices — the account's devices as a settings page (docs/every-device.md): a line on what
 * they are for and a quiet count, then **this device** as a card (its glyph, its name — edited in
 * place —, OS and version, the online dot, what it can do as chips, the hub and remote-control
 * switches), then **the other devices** as a grid of the same cards with "Online · just now" /
 * "Last seen 2 h ago", an `@name` hint for the ones that take tasks, *Ask* and an overflow menu
 * with *Forget*. Only this device: a card on how to add one. On a computer the card for its
 * own hands follows, as before. There is no "cloud computer" section — we have none.
 */
const DOWNLOAD_URL = "https://nanolika.ru/dl/";

export function DevicesScreen() {
  const { state, dispatch, openThread, setTab, toast } = useStore();
  const t = useT();
  const hub = state.hub;
  const hands = state.hands;
  const [refreshing, setRefreshing] = useState(false);

  const reload = async () => {
    try {
      const [h, hs] = await Promise.all([api.hub(), api.hands()]);
      dispatch({ type: "ws", msg: { kind: "hub", hub: h } });
      dispatch({ type: "ws", msg: { kind: "hands_state", hands: hs } });
    } catch {
      /* offline; the live socket fills it in */
    }
  };
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const refresh = async () => {
    setRefreshing(true);
    await reload();
    setRefreshing(false);
  };

  const ask = async (d: HubDevice) => {
    try {
      const r = await api.askDevice(d.id);
      dispatch({ type: "ws", msg: { kind: "thread", thread: r.thread } });
      openThread(r.thread.id);
      setTab("chat");
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const forget = (d: HubDevice) => {
    if (!window.confirm(t("Forget {name}? It can join again by signing in.", { name: d.name }))) return;
    void api.forgetDevice(d.id).then((h) => dispatch({ type: "ws", msg: { kind: "hub", hub: h } })).catch((e: Error) => toast(e.message));
  };

  const others = (hub?.devices ?? []).filter((d) => !d.this && d.kind !== "web");
  const signedIn = !!hub?.account.signed_in;
  const connected = hub?.state === "connected";
  // the count: this device (when it is on the hub) and the others
  const total = others.length + (hub && connected ? 1 : 0);
  const online = others.filter((d) => d.online).length + (hub && connected ? 1 : 0);

  return (
    <div className="flex h-full flex-col">
      <PageBar
        title={t("Devices")}
        actions={
          <button type="button" onClick={() => void refresh()} aria-label={t("Refresh")} title={t("Refresh")} className="flex h-9 w-9 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg">
            <RefreshCw size={17} className={cx(refreshing && "animate-spin")} />
          </button>
        }
      />
      <div className="@container flex-1 overflow-y-auto pb-8">
        {/* the header: what devices are for, and how many there are */}
        <div className="px-5 pb-4 pt-1">
          <p className="text-[14px] leading-[20px] text-muted">{t("Every device signed in to your account is a Muse of its own, on the same conversations. Type @ and a device's name in the chat to hand it work; a computer lends its hands.")}</p>
          {hub && signedIn && total > 0 && (
            <p className="mt-1.5 text-[12.5px] font-medium uppercase tracking-wide text-muted">
              {total === 1 ? t("1 device") : t("{n} devices", { n: total })} · {t("{n} online", { n: online })}
            </p>
          )}
        </div>

        <div className="px-4 pb-3">
          <CloudCard account={hub?.account ?? null} onChange={() => void reload()} />
        </div>

        <ChannelsCard />
        <DevicesList />

        {hub && (
          <div className="px-4">
            <SectionLabel>{t("This device")}</SectionLabel>
            <ThisDeviceCard hub={hub} hands={hands ?? undefined} onChange={() => void reload()} />
          </div>
        )}

        {/* the other devices */}
        <div className="px-4 pt-4">
          <SectionLabel>{t("Other devices")}</SectionLabel>
          {others.length === 0 ? (
            <EmptyDevices hub={hub ?? undefined} signedIn={signedIn} />
          ) : (
            <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
              {others.map((d) => (
                <DeviceCard key={d.id} device={d} onAsk={() => void ask(d)} onForget={() => forget(d)} />
              ))}
            </div>
          )}
          <p className="px-1 pt-3 text-[12.5px] leading-[18px] text-muted">{t("A chat addressed to a device runs there; you see every step here and answer its approvals.")}</p>
        </div>

        {hands && !hands.reason?.includes("phone") && (
          <div className="pt-4">
            <div className="px-4">
              <SectionLabel>{t("Hands")}</SectionLabel>
            </div>
            <HandsCard hands={hands} onChange={() => void reload()} />
          </div>
        )}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <p className="px-1 pb-2 text-[12.5px] font-medium uppercase tracking-wide text-muted">{children}</p>;
}

/** The messengers the account is reachable in: Telegram and MAX, each with its way out. */
function ChannelsCard() {
  const { toast } = useStore();
  const t = useT();
  const [linked, setLinked] = useState<Array<{ channel: string; hint: string; since: number }>>([]);
  const [own, setOwn] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const me = await api.cloudMe();
      setLinked(me.account.linked ?? []);
      setOwn(String(me.account.channel || ""));
      setError("");
    } catch {
      setError(t("Sign in to see the messengers."));
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const off = async (channel: string) => {
    if (!window.confirm(t("Turn off {channel}? The bot there will no longer know this chat.", { channel }))) return;
    setBusy(channel);
    try {
      await api.cloudUnlinkChannel(channel);
      await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const rows: Array<{ id: "telegram" | "max"; label: string }> = [
    { id: "telegram", label: "Telegram" },
    { id: "max", label: "MAX" },
  ];

  return (
    <div className="px-4 pt-4">
      <SectionLabel>{t("Messengers")}</SectionLabel>
      <div className="overflow-hidden rounded-2xl border border-border/70 bg-surface">
        {rows.map((row, i) => {
          const link = linked.find((l) => l.channel === row.id);
          return (
            <div key={row.id} className={cx("flex items-center gap-3 px-4 py-3", i > 0 && "border-t border-border/60")}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-fg/70" aria-hidden>
                <MessageSquare size={17} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[15px] font-medium leading-[20px]">{row.label}</p>
                <p className="truncate text-[12.5px] leading-[18px] text-muted">
                  {link ? link.hint || t("Connected") : t("Not connected")}
                </p>
              </div>
              {link && row.id !== own && (
                <button
                  type="button"
                  disabled={busy === row.id}
                  onClick={() => void off(row.id)}
                  className="h-8 rounded-full bg-surface-2 px-3 text-[13px] font-medium text-fg/80 hover:bg-surface-2/70 disabled:opacity-40"
                >
                  {t("Turn off")}
                </button>
              )}
              {link && row.id === own && (
                <span className="text-[12px] text-muted">{t("this is the account")}</span>
              )}
            </div>
          );
        })}
        {error && <p className="border-t border-border/60 px-4 py-2 text-[12.5px] text-muted">{error}</p>}
      </div>
      <p className="px-1 pt-2 text-[12.5px] leading-[18px] text-muted">
        {t("A computer or phone joins this account by the e-mail. Telegram and MAX are profiles of their own: each has its own chat with Lika and need not match this e-mail.")}
      </p>
    </div>
  );
}

/** Every sign-in of the account that is a person's own device, newest first, each with its way out. */
function DevicesList() {
  const { toast, state } = useStore();
  const t = useT();
  const [sessions, setSessions] = useState<CloudSession[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const r = await api.cloudSessions();
      // `runtime` and `sync` rows are the cloud's own machinery, not a device to switch off.
      setSessions(r.sessions.filter((s) => s.via !== "runtime" && s.via !== "sync"));
      setError("");
    } catch {
      setError(t("Sign in to see your devices."));
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const off = async (s: CloudSession) => {
    if (!window.confirm(t("Turn off {device}? It will have to sign in again.", { device: s.device }))) return;
    setBusy(s.prefix);
    try {
      const r = await api.cloudRevokeSession(s.prefix);
      setSessions(r.sessions.filter((x) => x.via !== "runtime" && x.via !== "sync"));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="px-4 pt-4">
      <SectionLabel>{t("Devices with {name} installed", { name: state.profile?.name ?? "NanoLika" })}</SectionLabel>
      <div className="overflow-hidden rounded-2xl border border-border/70 bg-surface">
        {sessions.length === 0 ? (
          <p className="px-4 py-3 text-[13px] text-muted">{error || t("No device is signed in yet.")}</p>
        ) : (
          sessions.map((s, i) => (
            <div key={s.prefix} className={cx("flex items-center gap-3 px-4 py-3", i > 0 && "border-t border-border/60")}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-fg/70" aria-hidden>
                {kindIcon(kindOfDevice(s.device), 17)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 truncate text-[15px] font-medium leading-[20px]">
                  <span className="truncate">{s.device || t("Unknown device")}</span>
                  {s.current && (
                    <span className="shrink-0 rounded-full bg-accent/12 px-2 py-0.5 text-[11px] font-medium text-accent">{t("this one")}</span>
                  )}
                </p>
                <p className="truncate text-[12.5px] leading-[18px] text-muted">
                  {[t("Last used {when}", { when: relativeTime(new Date((s.last_used_at ?? s.created_at) * 1000).toISOString()) }), viaLabel(s.via, t)]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              {!s.current && (
                <button
                  type="button"
                  disabled={busy === s.prefix}
                  onClick={() => void off(s)}
                  className="h-8 rounded-full bg-surface-2 px-3 text-[13px] font-medium text-fg/80 hover:bg-surface-2/70 disabled:opacity-40"
                >
                  {t("Turn off")}
                </button>
              )}
            </div>
          ))
        )}
      </div>
      <p className="px-1 pt-2 text-[12.5px] leading-[18px] text-muted">
        {t("A switched-off device is signed out of this account and has to sign in again. Its conversations stay here.")}
      </p>
    </div>
  );
}

/** Phone or computer, from the name a device reports (the app says what it runs on). */
function kindOfDevice(name: string): string {
  const low = (name || "").toLowerCase();
  if (/iphone|ipad|android|pixel|phone/.test(low)) return "phone";
  if (/mac|win|desktop|linux|pc/.test(low)) return "computer";
  return "web";
}

function viaLabel(via: string, t: typeof T): string {
  switch (via) {
    case "code":
      return t("signed in by code");
    case "password":
      return t("signed in by password");
    case "telegram":
    case "telegram_webapp":
      return "Telegram";
    case "max":
    case "max_webapp":
      return "MAX";
    case "session":
      return t("browser session");
    default:
      return via;
  }
}

/** The small grey line under a switch row inside a card. */
function Note({ children }: { children: ReactNode }) {
  return <p className="px-4 pb-3 text-[12.5px] leading-[18px] text-muted">{children}</p>;
}

/** The card's shell: 16 px corners, the surface on the page background, a hairline so it holds in both themes. */
function DeviceShell({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("relative rounded-2xl border border-border/70 bg-surface p-4", className)}>{children}</div>;
}

/** The platform glyph on its tile: phone, computer, browser. */
function Glyph({ kind, online }: { kind: string; online: boolean }) {
  return (
    <span className={cx("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl", online ? "bg-accent/10 text-accent" : "bg-surface-2 text-muted")} aria-hidden>
      {kindIcon(kind, 22)}
    </span>
  );
}

function OnlineDot({ online }: { online: boolean }) {
  return <span aria-hidden className={cx("inline-block h-2 w-2 shrink-0 rounded-full", online ? "bg-[#2fb344]" : "bg-muted/35")} />;
}

/** The relay's `last_seen` is Unix seconds (a number, or the digits as a string); older rows carried an ISO time. */
function seenAt(raw: string | number | undefined): string {
  if (raw === undefined || raw === null || raw === "") return "";
  const n = typeof raw === "number" ? raw : /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : NaN;
  return Number.isFinite(n) ? new Date(n * 1000).toISOString() : String(raw);
}

/** "Online · just now" / "Last seen 2 h ago" / "Offline". An online device's `last_seen` is its last hello, which may be hours old: it is shown only while fresh. */
function presence(d: Pick<HubDevice, "online" | "last_seen">, t: typeof T): string {
  const iso = seenAt(d.last_seen);
  const when = relativeTime(iso);
  if (d.online) return iso && Date.now() - new Date(iso).getTime() < 120_000 ? `${t("Online")} · ${t("just now")}` : t("Online");
  return when ? t("Last seen {when}", { when }) : t("Offline");
}

/** What a device can do, from the hub actions it announces — our names, in a fixed order. */
export function capabilities(actions: readonly string[] | undefined, t: (s: string) => string, extra: string[] = []): string[] {
  const set = new Set(actions ?? []);
  const out: string[] = [...extra];
  if (set.has("task")) out.push(t("Tasks"));
  if (set.has("shell")) out.push(t("Commands"));
  if (set.has("files") || set.has("file.get") || set.has("file.put")) out.push(t("Files"));
  if (set.has("screen")) out.push(t("Screenshot"));
  if (set.has("open")) out.push(t("Open links"));
  if ([...set].some((a) => a.startsWith("coding."))) out.push(t("Coding agents"));
  if (set.has("notify")) out.push(t("Notifications"));
  return out;
}

function Chips({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((c) => (
        <span key={c} className="rounded-full bg-surface-2 px-2.5 py-1 text-[12px] font-medium text-fg/80">
          {c}
        </span>
      ))}
    </div>
  );
}

/** The device line under the name: OS · version. */
function osLine(d: { os?: string; version?: string }): string {
  return [d.os || "", d.version || ""].filter(Boolean).join(" · ");
}

function ThisDeviceCard({ hub, hands, onChange }: { hub: HubView; hands?: HandsStatus; onChange: () => void }) {
  const { dispatch, toast } = useStore();
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(hub.device.name);
  const [busy, setBusy] = useState(false);
  const signedIn = hub.account.signed_in;
  const connected = hub.state === "connected";
  // the relay's own row for this device carries the OS and the version
  const self = hub.devices.find((d) => d.this);

  const update = async (body: { enabled?: boolean; remote_control?: boolean; memory_sharing?: boolean; name?: string }) => {
    setBusy(true);
    try {
      const h = await api.updateHub(body);
      dispatch({ type: "ws", msg: { kind: "hub", hub: h } });
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const status = !signedIn
    ? t("Signed out")
    : !hub.enabled
      ? t("Off")
      : connected
        ? t("Online")
        : hub.state === "refused"
          ? t("Refused")
          : t("Connecting…");
  const can = capabilities(hub.device.actions, t, hands?.enabled && hands.available ? [t("Hands")] : []);

  return (
    <DeviceShell>
      <div className="flex items-start gap-3.5">
        <Glyph kind={hub.device.kind} online={connected} />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (!draft.trim()) return;
                void update({ name: draft.trim() }).then(() => setEditing(false));
              }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={60}
                autoFocus
                aria-label={t("This device's name")}
                placeholder={t("A name your other devices will see")}
                className="h-9 min-w-0 flex-1 rounded-xl bg-surface-2 px-3 text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button type="submit" disabled={busy || !draft.trim()} aria-label={t("Save")} className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-fg disabled:opacity-40">
                <Check size={16} />
              </button>
              <button type="button" onClick={() => setEditing(false)} aria-label={t("Cancel")} className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-muted">
                <X size={16} />
              </button>
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[16px] font-semibold leading-[22px]">{hub.device.name}</span>
              <button
                type="button"
                onClick={() => {
                  setDraft(hub.device.name);
                  setEditing(true);
                }}
                aria-label={t("Rename this device")}
                title={t("Rename this device")}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
              >
                <Pencil size={13} />
              </button>
            </div>
          )}
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] leading-[18px] text-muted">
            <OnlineDot online={connected} />
            <span className="truncate">{[status, osLine(self ?? {})].filter(Boolean).join(" · ")}</span>
          </p>
          {can.length > 0 && (
            <div className="mt-3">
              <Chips items={can} />
            </div>
          )}
        </div>
      </div>

      <div className="-mx-4 -mb-2 mt-4 border-t border-border/60">
        <MuseSwitchRow label={t("Join the hub")} checked={hub.enabled && signedIn} disabled={!signedIn || busy} onChange={(v) => void update({ enabled: v })} />
        <Note>{signedIn ? t("Reachable by your other devices while this runs.") : t("Sign in first.")}</Note>
        <MuseDivider inset={16} />
        <MuseSwitchRow label={t("My other devices may operate it")} checked={hub.remote_control} disabled={busy} onChange={(v) => void update({ remote_control: v })} />
        <Note>{t("They can run commands, read files and see its screen here; each step still goes through the Sentinel.")}</Note>
        <MuseDivider inset={16} />
        <MuseSwitchRow label={t("Share memory with my devices")} checked={hub.memory_sharing} disabled={!signedIn || busy} onChange={(v) => void update({ memory_sharing: v })} />
        <Note>{t("Other signed-in devices can read, save and forget shared facts. Conversations and tasks stay separate.")}</Note>
      </div>
      {(hub.state === "refused" || hub.state === "disconnected") && hub.detail && (
        <div className="mt-3 rounded-xl bg-amber-500/12 px-3 py-2 text-[12.5px] text-amber-700 dark:text-amber-300">{hub.detail}</div>
      )}
    </DeviceShell>
  );
}

/** One of the other devices: the same anatomy, *Ask* when it is online, *Forget* behind the dots. */
function DeviceCard({ device: d, onAsk, onForget }: { device: HubDevice; onAsk: () => void; onForget: () => void }) {
  const t = useT();
  const can = capabilities(d.actions, t);
  const takesTasks = d.online && (d.actions ?? []).includes("task");
  return (
    <DeviceShell className="flex flex-col gap-3">
      <div className="flex items-start gap-3.5">
        <Glyph kind={d.kind} online={d.online} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[16px] font-semibold leading-[22px]">{d.name}</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] leading-[18px] text-muted">
            <OnlineDot online={d.online} />
            <span className="truncate">{[presence(d, t), osLine(d)].filter(Boolean).join(" · ")}</span>
          </p>
        </div>
        <OverflowMenu label={t("More about {name}", { name: d.name })} items={[{ label: t("Forget this device"), onClick: onForget, danger: true }]} />
      </div>
      <Chips items={can} />
      {takesTasks && <p className="text-[12.5px] leading-[18px] text-muted">{t("Type @{name} in the chat to hand it work.", { name: d.name })}</p>}
      {d.online && (
        <button type="button" onClick={onAsk} className="flex h-9 w-fit items-center gap-1.5 rounded-full bg-accent/10 px-3.5 text-[13.5px] font-medium text-accent hover:bg-accent/15">
          <MessageSquare size={14} /> {t("Ask")}
        </button>
      )}
    </DeviceShell>
  );
}

/** The dots at a card's corner and the small menu under them. */
function OverflowMenu({ label, items }: { label: string; items: Array<{ label: string; onClick: () => void; danger?: boolean }> }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useMenuKeys(ref, open, () => setOpen(false));
  return (
    <div ref={ref} className="relative -mr-1.5 -mt-1">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-label={label} aria-haspopup="menu" aria-expanded={open} className="flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg">
        <MoreHorizontal size={18} />
      </button>
      {open && (
        <ul role="menu" className="absolute right-0 top-9 z-30 min-w-[180px] overflow-hidden rounded-2xl border border-border bg-surface py-1 shadow-xl">
          {items.map((it) => (
            <li key={it.label}>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  it.onClick();
                }}
                className={cx("w-full px-4 py-2.5 text-left text-[14px] hover:bg-surface-2", it.danger && "text-red-600 dark:text-red-400")}
              >
                {it.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Only this device: how to add one; signed out or off the hub, why nothing is listed. */
function EmptyDevices({ hub, signedIn }: { hub: HubView | undefined; signedIn: boolean }) {
  const t = useT();
  const ready = signedIn && hub?.state === "connected";
  return (
    <DeviceShell className="flex flex-col gap-3">
      <div className="flex items-start gap-3.5">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-muted" aria-hidden>
          <Smartphone size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold leading-[20px]">{ready ? t("Add a device") : t("No other device yet")}</p>
          <p className="mt-1 text-[13px] leading-[18px] text-muted">
            {!signedIn
              ? t("Sign in above to see them.")
              : !ready
                ? hubStateLabel(hub?.state ?? "", t)
                : hub?.account.hint
                  ? t("Install NanoLika on your phone, Mac or PC and sign in as {hint}; it appears here on its own.", { hint: hub.account.hint })
                  : t("Install NanoLika on your phone, Mac or PC and sign in with the same account; it appears here on its own.")}
          </p>
        </div>
      </div>
      {ready && (
        <a href={DOWNLOAD_URL} target="_blank" rel="noopener noreferrer" className="flex h-9 w-fit items-center gap-1.5 rounded-full bg-accent/10 px-3.5 text-[13.5px] font-medium text-accent hover:bg-accent/15">
          {t("Get the apps")}
        </a>
      )}
    </DeviceShell>
  );
}

function HandsCard({ hands, onChange }: { hands: HandsStatus; onChange: () => void }) {
  const { dispatch, toast, state } = useStore();
  const t = useT();
  const [busy, setBusy] = useState(false);
  const museName = state.profile?.name ?? "NanoLika";

  const set = async (body: { enabled?: boolean; backend?: string; mode?: string }) => {
    setBusy(true);
    try {
      const h = await api.setHands(body);
      dispatch({ type: "ws", msg: { kind: "hands_state", hands: h } });
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const status = hands.task_active ? t("Working") : hands.enabled ? (hands.available ? t("On") : t("Unavailable")) : t("Off");

  return (
    <>
      <MuseCard className="border border-border/70">
        <MuseSwitchRow icon={<Hand size={22} />} label={t("Hands on this computer")} value={status} checked={hands.enabled} disabled={busy} onChange={(v) => void set({ enabled: v })} />
        <MuseCaption className="pb-3 pt-0">
          {hands.task_active
            ? hands.task_text || t("On the screen now")
            : hands.available
              ? t("It looks at a screenshot and clicks by position; paying, sending and deleting ask you first.")
              : hands.reason || t("Nothing can drive this screen yet.")}
        </MuseCaption>
        <MuseDivider inset={16} />
        <div className="flex items-center gap-2 px-4 py-3 text-[13px] text-muted">
          <span>{t("Driver")}:</span>
          {(["auto", "desktop", "pyautogui", "xdotool"] as const).map((b) => (
            <button
              key={b}
              type="button"
              disabled={busy}
              aria-pressed={(hands.backend ?? "auto") === b}
              onClick={() => void set({ backend: b })}
              className={cx("rounded-full px-2.5 py-1", (hands.backend ?? "auto") === b ? "bg-fg text-bg" : "bg-surface-2 text-fg")}
            >
              {b === "auto" ? t("Auto") : b === "desktop" ? t("Desktop") : b}
            </button>
          ))}
        </div>
        {(hands.device?.platform === "darwin" || hands.window?.available) && (
          <>
            <MuseDivider inset={16} />
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 text-[13px] text-muted">
              <span>{t("Where")}:</span>
              {(["auto", "window", "screen"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={busy}
                  aria-pressed={(hands.mode ?? "auto") === m}
                  onClick={() => void set({ mode: m })}
                  className={cx("rounded-full px-2.5 py-1", (hands.mode ?? "auto") === m ? "bg-fg text-bg" : "bg-surface-2 text-fg")}
                >
                  {m === "auto" ? t("Auto") : m === "window" ? t("One window") : t("Whole screen")}
                </button>
              ))}
            </div>
            <MuseCaption className="pb-3 pt-0">
              {hands.window?.active && hands.window.app
                ? t("Working in {app}'s window; the mouse stays yours.", { app: hands.window.app })
                : hands.window?.reason && (hands.mode ?? "auto") !== "screen"
                  ? t("On the whole screen for now: {reason}", { reason: hands.window.reason })
                  : t("One window: the hands work inside the app they were given, send clicks and keys to it alone and leave your mouse alone. Whole screen: the system mouse, like other platforms. Auto picks one window as soon as an app is named.")}
            </MuseCaption>
          </>
        )}
        {hands.task_active && (
          <>
            <MuseDivider inset={16} />
            <MuseRow icon={<Square size={18} fill="currentColor" />} label={t("Stop the hands")} onClick={() => void api.stopHands().catch((e: Error) => toast(e.message))} chevron={false} />
          </>
        )}
      </MuseCard>
      {hands.enabled && hands.device?.platform === "darwin" && (
        <MuseCaption>{t("On a Mac, allow NanoLika under System Settings → Privacy & Security → Accessibility and Screen Recording when macOS asks; without them clicks do nothing and the screenshot is black. The first action in each app asks you once (\"Let {name} use Safari?\") and the answer is kept under Permissions.", { name: museName })}</MuseCaption>
      )}
    </>
  );
}

export function kindIcon(kind: string, size = 18): ReactNode {
  switch (kind) {
    case "phone":
      return <Smartphone size={size} />;
    case "computer":
      return <Laptop size={size} />;
    case "web":
      return <Globe size={size} />;
    default:
      return <Wifi size={size} />;
  }
}

export function hubStateLabel(state: string, t: (s: string) => string): string {
  switch (state) {
    case "connected":
      return t("On the hub");
    case "connecting":
    case "disconnected":
      return t("Connecting to the hub…");
    case "refused":
      return t("The hub refused this device. Sign in again.");
    case "signed_out":
      return t("Not signed in.");
    case "off":
    case "stopped":
      return t("The hub is off on this device.");
    default:
      return state;
  }
}
