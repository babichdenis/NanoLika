/**
 * A chat that talks straight to the relay — the first slice of the cloud mode.
 *
 * No runtime, no store: the browser holds the account key and streams from
 * `cloud.nanolika.ru/v1`. It is deliberately small; when it is proven, the same transport
 * feeds the ordinary screens.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Send } from "lucide-react";
import { chat, cloudToken, CloudError, isRecord, me, models, relayBase, setCloudToken, type CloudMessage, type CloudModel } from "./client";
import { announce, signInFromWebApp } from "./webapp";

interface Turn {
  role: "user" | "assistant";
  text: string;
  reasoning?: string;
  pending?: boolean;
}

export function CloudChat() {
  const [token, setToken] = useState(() => cloudToken());
  const [account, setAccount] = useState<string>("");
  const [list, setList] = useState<CloudModel[]>([]);
  const [model, setModel] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    announce();
    if (tokenRef.current) return;
    // Opened from Telegram or MAX: the messenger's signed payload is the sign-in.
    signInFromWebApp()
      .then((key) => {
        if (key) setToken(key);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Мессенджер не подтвердил вход."));
  }, []);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => {
      try {
        const [menu, who] = await Promise.all([models(), me()]);
        if (!alive) return;
        setList(menu);
        setModel((current) => current || menu[0]?.id || "");
        const spend = isRecord(who) && isRecord(who.spend) ? who.spend : {};
        const hint = isRecord(who) ? String(who.hint || "") : "";
        setAccount(hint || String(spend.left_rub ?? ""));
        setError("");
      } catch (e) {
        if (alive) setError(e instanceof CloudError ? e.message : "Не удалось прочитать меню моделей.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [token]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  const visible = useMemo(() => turns.filter((turn) => turn.text || turn.reasoning || turn.pending), [turns]);

  async function send() {
    const text = draft.trim();
    if (!text || busy || !model) return;
    setDraft("");
    setError("");
    setBusy(true);
    const history: CloudMessage[] = [
      ...turns.filter((turn) => !turn.pending).map((turn) => ({ role: turn.role, content: turn.text })),
      { role: "user", content: text },
    ];
    setTurns((all) => [...all, { role: "user", text }, { role: "assistant", text: "", pending: true }]);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const result = await chat(history, model, {
        signal: controller.signal,
        onDelta: (chunk) =>
          setTurns((all) => {
            const next = [...all];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, text: last.text + chunk, pending: false };
            return next;
          }),
        onReasoning: (chunk) =>
          setTurns((all) => {
            const next = [...all];
            const last = next[next.length - 1];
            if (last?.role === "assistant") next[next.length - 1] = { ...last, reasoning: (last.reasoning || "") + chunk };
            return next;
          }),
      });
      setTurns((all) => {
        const next = [...all];
        const last = next[next.length - 1];
        if (last?.role === "assistant") next[next.length - 1] = { ...last, text: result.text, pending: false };
        return next;
      });
    } catch (e) {
      if ((e as Error)?.name === "AbortError") {
        setTurns((all) => {
          const next = [...all];
          const last = next[next.length - 1];
          if (last?.role === "assistant") next[next.length - 1] = { ...last, pending: false };
          return next;
        });
      } else {
        setError(e instanceof CloudError ? e.message : "Ответ не пришёл.");
        setTurns((all) => all.filter((turn) => !turn.pending));
      }
    } finally {
      abort.current = null;
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 p-6">
        <h1 className="text-lg font-semibold">Облако nanoLika</h1>
        <p className="text-sm text-muted">Вставьте ключ аккаунта (он же ключ nanoLika API из кабинета).</p>
        <input
          type="password"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="nm_…"
          className="rounded-xl border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button
          type="button"
          className="rounded-xl bg-fg px-4 py-2 text-sm font-medium text-bg disabled:opacity-50"
          disabled={!draft.trim()}
          onClick={() => {
            setCloudToken(draft.trim());
            setToken(draft.trim());
            setDraft("");
          }}
        >
          Войти
        </button>
        <p className="text-xs text-muted">Релей: {relayBase()}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">nanoLika Cloud</p>
          <p className="truncate text-xs text-muted">{account || relayBase()}</p>
        </div>
        <select
          value={model}
          onChange={(event) => setModel(event.target.value)}
          className="max-w-[45%] rounded-lg border border-border bg-surface px-2 py-1 text-xs"
        >
          {list.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name || entry.id}
            </option>
          ))}
        </select>
      </header>

      <main className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {visible.length === 0 && <p className="text-sm text-muted">Напишите первое сообщение — оно уйдёт в облако.</p>}
        {visible.map((turn, index) => (
          <div key={index} className={turn.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={
                turn.role === "user"
                  ? "max-w-[85%] whitespace-pre-wrap rounded-2xl bg-fg px-3 py-2 text-sm text-bg"
                  : "max-w-[85%] whitespace-pre-wrap rounded-2xl bg-surface px-3 py-2 text-sm"
              }
            >
              {turn.reasoning && <p className="mb-1 text-xs italic text-muted">{turn.reasoning}</p>}
              {turn.text || (turn.pending ? <Loader2 className="animate-spin" size={14} /> : null)}
            </div>
          </div>
        ))}
        {error && <p className="rounded-xl bg-amber-500/12 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">{error}</p>}
        <div ref={bottom} />
      </main>

      <form
        className="flex items-end gap-2 border-t border-border px-4 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          rows={1}
          placeholder="Напишите Лике…"
          className="max-h-40 flex-1 resize-none rounded-xl border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="rounded-xl bg-fg p-2 text-bg disabled:opacity-40"
          aria-label="Отправить"
        >
          {busy ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
        </button>
      </form>
    </div>
  );
}
