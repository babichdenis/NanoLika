/**
 * The chat's model picker, a skeleton in the manner of the OpenCode dialog: a searchable
 * list on the left (favourites, recent, the account's models) and the model's details on
 * the right (its reasoning levels and a favourite star). Picking a model makes it the
 * account's chat model; the chat reloads once it is saved.
 */
import { Check, ChevronRight, Loader2, Search, Star, X } from "lucide-react";
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
  reasoning?: { levels?: string[]; default?: string; wire?: string } | null;
}

interface ModelsAnswer {
  current: { provider: string; model: string; cloud: boolean };
  models: AccountModel[];
}

const FAV_KEY = "nm.model.favorites";
const RECENT_KEY = "nm.model.recent";

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
  const [picked, setPicked] = useState<AccountModel | null>(null);
  const [favorites, setFavorites] = useState<string[]>(() => readList(FAV_KEY));
  const [recent, setRecent] = useState<string[]>(() => readList(RECENT_KEY));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError("");
    void api
      .models()
      .then((data) => {
        setAnswer(data);
        const first = data.models.find((m) => m.id === current) ?? data.models.find((m) => m.kind !== "image" && m.kind !== "video") ?? null;
        setPicked(first);
      })
      .catch((e: Error) => setError(e.message || t("Could not load the models.")));
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
    const match = (m: AccountModel) => !q || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q) || m.vendor.toLowerCase().includes(q);
    const byId = new Map(all.map((m) => [m.id, m]));
    const fav = favorites.map((id) => byId.get(id)).filter((m): m is AccountModel => !!m).filter(match);
    const rec = recent.map((id) => byId.get(id)).filter((m): m is AccountModel => !!m && !favorites.includes(m.id)).filter(match);
    const rest = all.filter((m) => !favorites.includes(m.id) && !recent.includes(m.id)).filter(match);
    return [
      { title: t("Favorites"), rows: fav },
      { title: t("Recent"), rows: rec },
      { title: t("All models"), rows: rest },
    ].filter((group) => group.rows.length > 0);
  }, [answer, favorites, recent, query, t]);

  const toggleFavorite = (id: string) => {
    const next = favorites.includes(id) ? favorites.filter((x) => x !== id) : [id, ...favorites].slice(0, 24);
    setFavorites(next);
    writeList(FAV_KEY, next);
  };

  const choose = async (model: AccountModel) => {
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

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/35 px-3 py-10" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("Choose a model")}
        className="flex max-h-[80vh] w-full max-w-[860px] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-[0_24px_80px_-20px_rgba(0,0,0,0.45)]"
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
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_280px]">
          <div className="min-h-0 overflow-y-auto px-2 py-2">
            {!answer && !error && (
              <div className="flex items-center gap-2 px-3 py-6 text-[13.5px] text-muted">
                <Loader2 size={15} className="animate-spin" /> {t("Loading models…")}
              </div>
            )}
            {error && <div className="px-3 py-4 text-[13.5px] text-rose-600">{error}</div>}
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
                      {model.free && <span className="rounded-full bg-accent/12 px-2 py-0.5 text-[10.5px] font-medium text-accent">{t("Free")}</span>}
                      {model.vendor && <span className="shrink-0 text-[11.5px] text-muted">{model.vendor}</span>}
                      <ChevronRight size={14} className="shrink-0 text-muted" />
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          <aside className="border-l border-border px-4 py-4">
            {picked ? (
              <div className="flex h-full flex-col gap-3">
                <div>
                  <div className="text-[15px] font-semibold">{picked.name}</div>
                  <div className="text-[12.5px] text-muted">{picked.vendor || picked.id}</div>
                </div>
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
                <div className="mt-auto">
                  <button
                    type="button"
                    disabled={busy || picked.id === current}
                    onClick={() => void choose(picked)}
                    className="w-full rounded-xl bg-accent px-4 py-2.5 text-[14px] font-medium text-white disabled:opacity-50"
                  >
                    {busy ? t("Saving…") : picked.id === current ? t("Current model") : t("Use this model")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-[13px] text-muted">{t("Pick a model to see what it can do.")}</div>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
