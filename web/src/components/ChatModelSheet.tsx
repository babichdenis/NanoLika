/**
 * The chat's model picker in the manner of the OpenCode dialog: a searchable list on the
 * left (kind filters, favourites, recent, the account's models) and the model's card on
 * the right — price per million tokens, context, reasoning levels, plans and the doors to
 * the cabinet and the top-up page. Picking a model makes it the account's chat model.
 */
import { Check, ChevronRight, CreditCard, Loader2, Search, Star, UserRound, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { cx } from "../util";

export interface AccountModel {
  id: string;
  name: string;
  vendor: string;
  kind: string;
  free: boolean;
  locked?: boolean;
  reasoning?: { levels?: string[]; default?: string; wire?: string } | null;
  sell?: { currency?: string; in?: number; cached_in?: number; out?: number } | null;
  context?: number | null;
}

interface ModelsAnswer {
  current: { provider: string; model: string; cloud: boolean };
  models: AccountModel[];
}

type Kind = "all" | "chat" | "image" | "video" | "audio";

const FAV_KEY = "nm.model.favorites";
const RECENT_KEY = "nm.model.recent";
const PRICING_URL = "https://nanolika.ru/pricing.html";
const ACCOUNT_URL = "/web/account/?source=web";

function readList(key: string): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeList(key: string, list: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(list));
  } catch {
    // storage blocked: the choice lasts for this page
  }
}

function kindOf(model: AccountModel): Kind {
  const kind = (model.kind || "").toLowerCase();
  if (kind === "image" || kind === "video" || kind === "audio") return kind;
  return "chat";
}

function money(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return value >= 100 ? value.toFixed(0) : value.toFixed(2).replace(".", ",");
}

function contextLabel(tokens: number | null | undefined): string {
  if (!tokens || tokens <= 0) return "";
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
  return String(tokens);
}

export function ChatModelSheet({
  open,
  current,
  onClose,
  onPicked,
}: {
  open: boolean;
  current: string;
  onClose: () => void;
  onPicked: (model: AccountModel) => void;
}) {
  const t = useT();
  const [answer, setAnswer] = useState<ModelsAnswer | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<Kind>("all");
  const [picked, setPicked] = useState<AccountModel | null>(null);
  const [favorites, setFavorites] = useState<string[]>(() => readList(FAV_KEY));
  const [recent, setRecent] = useState<string[]>(() => readList(RECENT_KEY));
  const [plans, setPlans] = useState<Array<{ id: string; name: string; amount: number }>>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError("");
    void api
      .models()
      .then((data) => {
        setAnswer(data);
        const first =
          data.models.find((m) => m.id === current) ??
          data.models.find((m) => kindOf(m) === "chat") ??
          null;
        setPicked(first);
      })
      .catch((e: Error) => setError(e.message || t("Could not load the models.")));
    void fetch("/v1/payments/config", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        const rows = data?.plans;
        if (!rows || typeof rows !== "object") return;
        setPlans(
          Object.entries(rows as Record<string, { name?: string; amount?: number }>).map(([id, row]) => ({
            id,
            name: String(row?.name || id),
            amount: Number(row?.amount || 0) / 100,
          })),
        );
      })
      .catch(() => undefined);
  }, [open, current, t]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const groups = useMemo(() => {
    const all = answer?.models ?? [];
    const q = query.trim().toLowerCase();
    const match = (m: AccountModel) =>
      (kind === "all" || kindOf(m) === kind) &&
      (!q || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q) || m.vendor.toLowerCase().includes(q));
    const byId = new Map(all.map((m) => [m.id, m]));
    const fav = favorites.map((id) => byId.get(id)).filter((m): m is AccountModel => !!m).filter(match);
    const rec = recent
      .map((id) => byId.get(id))
      .filter((m): m is AccountModel => !!m && !favorites.includes(m.id))
      .filter(match);
    const rest = all.filter((m) => !favorites.includes(m.id) && !recent.includes(m.id)).filter(match);
    return [
      { title: t("Favorites"), rows: fav },
      { title: t("Recent"), rows: rec },
      { title: t("All models"), rows: rest },
    ].filter((group) => group.rows.length > 0);
  }, [answer, favorites, recent, query, kind, t]);

  const toggleFavorite = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter((x) => x !== id) : [id, ...favorites].slice(0, 24);
    setFavorites(next);
    writeList(FAV_KEY, next);
  };

  const choose = async (model: AccountModel) => {
    if (model.locked) return;
    setBusy(true);
    setError("");
    try {
      await api.cloudUseAsModel(model.id);
      const nextRecent = [model.id, ...recent.filter((x) => x !== model.id)].slice(0, 8);
      setRecent(nextRecent);
      writeList(RECENT_KEY, nextRecent);
      onPicked(model);
      onClose();
    } catch (e) {
      setError((e as Error).message || t("Could not change the model."));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  const kinds: Array<{ id: Kind; label: string }> = [
    { id: "all", label: t("All") },
    { id: "chat", label: t("Text") },
    { id: "image", label: t("Images") },
    { id: "video", label: t("Video") },
    { id: "audio", label: t("Audio") },
  ];
  const pill = (active: boolean) =>
    cx(
      "rounded-full border px-3 py-1 text-[12.5px] transition",
      active ? "border-accent bg-accent/10 text-accent" : "border-border text-fg/70 hover:bg-surface-2",
    );

  const inPrice = money(picked?.sell?.in);
  const outPrice = money(picked?.sell?.out);
  const currency = picked?.sell?.currency || "RUB";

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/35 px-3 py-10" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("Choose a model")}
        className="flex max-h-[84vh] w-full max-w-[920px] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-[0_24px_80px_-20px_rgba(0,0,0,0.45)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Search size={16} className="text-muted" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("Search models")}
            aria-label={t("Search models")}
            className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted"
          />
          <button type="button" onClick={onClose} aria-label={t("Close")} className="rounded-lg p-1 text-muted hover:bg-surface-2 hover:text-fg">
            <X size={16} />
          </button>
        </div>
        <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">
          {kinds.map((item) => (
            <button key={item.id} type="button" onClick={() => setKind(item.id)} className={pill(kind === item.id)} aria-pressed={kind === item.id}>
              {item.label}
            </button>
          ))}
        </div>
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-h-0 overflow-y-auto px-2 py-2">
            {!answer && !error && (
              <div className="flex items-center gap-2 px-3 py-6 text-[13.5px] text-muted">
                <Loader2 size={15} className="animate-spin" /> {t("Loading models…")}
              </div>
            )}
            {error && <div className="px-3 py-4 text-[13.5px] text-rose-600">{error}</div>}
            {!error && answer && groups.length === 0 && (
              <div className="px-3 py-4 text-[13.5px] text-muted">{t("Nothing found.")}</div>
            )}
            {groups.map((group) => (
              <div key={group.title} className="mb-2">
                <div className="px-3 pb-1 pt-2 text-[11.5px] font-semibold uppercase tracking-wide text-muted">{group.title}</div>
                {group.rows.map((model) => {
                  const selected = picked?.id === model.id;
                  const active = model.id === current;
                  return (
                    <button
                      key={model.id}
                      type="button"
                      onClick={() => setPicked(model)}
                      className={cx(
                        "flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left",
                        selected ? "bg-surface-2" : "hover:bg-surface-2/70",
                      )}
                    >
                      <span className="w-4 shrink-0 text-accent">{active ? <Check size={14} /> : null}</span>
                      <span className="min-w-0 flex-1 truncate text-[14px]">{model.name}</span>
                      {model.locked && <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10.5px] font-medium text-amber-700">{t("Lika+")}</span>}
                      {model.vendor && <span className="shrink-0 text-[11.5px] text-muted">{model.vendor}</span>}
                      <ChevronRight size={14} className="shrink-0 text-muted" />
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto border-l border-border px-4 py-4">
            {picked ? (
              <>
                <div>
                  <div className="text-[15px] font-semibold">{picked.name}</div>
                  <div className="text-[12.5px] text-muted">{picked.vendor || picked.id}</div>
                  {picked.locked && (
                    <div className="mt-2 rounded-lg bg-amber-500/12 px-2.5 py-1.5 text-[12px] text-amber-800">
                      {t("Unavailable on your plan")}. {t("Opens on the Lika+ plan")}.{" "}
                      <a href={PRICING_URL} target="_blank" rel="noreferrer" className="underline">
                        {t("Compare plans")}
                      </a>
                    </div>
                  )}
                </div>
                {inPrice || outPrice ? (
                  <div className="rounded-xl bg-surface-2/60 px-3 py-2.5">
                    <div className="text-[11.5px] font-semibold uppercase tracking-wide text-muted">{t("Price per 1M tokens")}</div>
                    <div className="mt-1 flex items-baseline justify-between gap-2 text-[13px]">
                      <span>{t("Input")}</span>
                      <span className="font-medium">{inPrice ? `${inPrice} ₽` : "—"}</span>
                    </div>
                    <div className="flex items-baseline justify-between gap-2 text-[13px]">
                      <span>{t("Output")}</span>
                      <span className="font-medium">{outPrice ? `${outPrice} ₽` : "—"}</span>
                    </div>
                    {currency !== "RUB" && <div className="mt-1 text-[11px] text-muted">{currency}</div>}
                  </div>
                ) : null}
                {contextLabel(picked.context) && (
                  <div className="text-[12.5px] text-muted">
                    {t("Context")}: {contextLabel(picked.context)} {t("tokens")}
                  </div>
                )}
                {picked.reasoning?.levels?.length ? (
                  <div>
                    <div className="mb-1 text-[11.5px] font-semibold uppercase tracking-wide text-muted">{t("Reasoning")}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {picked.reasoning.levels.map((level) => (
                        <span key={level} className="rounded-lg border border-border px-2.5 py-1 text-[12.5px]">
                          {level}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : null}
                <button
                  type="button"
                  onClick={() => toggleFavorite(picked.id)}
                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-surface-2"
                >
                  <Star size={15} className={favorites.includes(picked.id) ? "fill-amber-400 text-amber-500" : "text-muted"} />
                  {favorites.includes(picked.id) ? t("Remove from favorites") : t("Add to favorites")}
                </button>
                {plans.length > 0 && (
                  <div>
                    <div className="mb-1 text-[11.5px] font-semibold uppercase tracking-wide text-muted">{t("Plans")}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {plans.map((plan) => (
                        <a key={plan.id} href={PRICING_URL} target="_blank" rel="noreferrer" className={pill(false)}>
                          {plan.name} · {plan.amount.toFixed(0)} ₽
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                <div className="mt-1 flex flex-col gap-1 border-t border-border pt-2">
                  <a href={PRICING_URL} target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-surface-2">
                    <CreditCard size={15} className="text-muted" /> {t("Top up")}
                  </a>
                  <a href={ACCOUNT_URL} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] hover:bg-surface-2">
                    <UserRound size={15} className="text-muted" /> {t("Account page")}
                  </a>
                </div>
                <div className="mt-auto">
                  <button
                    type="button"
                    disabled={busy || picked.locked || picked.id === current}
                    onClick={() => void choose(picked)}
                    className="w-full rounded-xl bg-accent px-4 py-2.5 text-[14px] font-medium text-white disabled:opacity-50"
                  >
                    {picked.locked ? t("Available on Lika+") : busy ? t("Saving…") : picked.id === current ? t("Current model") : t("Use this model")}
                  </button>
                </div>
              </>
            ) : (
              <div className="text-[13px] text-muted">{t("Pick a model to see what it can do.")}</div>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
