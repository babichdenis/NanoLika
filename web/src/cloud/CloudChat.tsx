/**
 * A chat that talks straight to the relay — the cloud face of the single client.
 *
 * No runtime, no store: the browser holds the account key and streams from
 * `cloud.nanolika.ru/v1`. The look follows the product: the fairy, the warm paper, the
 * green bubble for the person's words.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Plus, Send } from "lucide-react";
import { useT } from "../i18n";
import {
  chat,
  cloudToken,
  CloudError,
  isRecord,
  me,
  models,
  relayBase,
  setCloudToken,
  uploadAttachment,
  type CloudMessage,
  type CloudModel,
  type UploadedFile,
} from "./client";
import { catalog, messages as history, publish, type ConversationMeta, type SyncedMessage } from "./sync";
import { announce, applyMessengerTheme, loadTelegramSdk, signInFromWebApp, surface, wireBackButton } from "./webapp";

interface Turn {
  role: "user" | "assistant";
  text: string;
  reasoning?: string;
  pending?: boolean;
}

const TEXT_LIMIT = 20_000;

const isTextFile = (file: File) =>
  /^text\//i.test(file.type || "") ||
  /\.(txt|md|markdown|csv|tsv|json|log|ya?ml|xml|html?|css|js|mjs|ts|tsx|jsx|py|rb|go|rs|java|c|h|cpp|sh|bash|zsh|sql|ini|toml)$/i.test(
    file.name || "",
  );
const isImageFile = (file: File) => /^image\//i.test(file.type || "");

export function CloudChat() {
  const t = useT();
  const [token, setToken] = useState(() => cloudToken());
  const [account, setAccount] = useState("");
  const [list, setList] = useState<CloudModel[]>([]);
  const [model, setModel] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [chats, setChats] = useState<ConversationMeta[]>([]);
  const [openChat, setOpenChat] = useState<ConversationMeta | null>(null);
  const [historyRows, setHistoryRows] = useState<SyncedMessage[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [pending, setPending] = useState<File[]>([]);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const cidRef = useRef<string>("");
  const startedRef = useRef<number>(0);
  const tokenRef = useRef(token);
  const tRef = useRef(t);

  useEffect(() => {
    tokenRef.current = token;
    tRef.current = t;
  }, [token, t]);

  useEffect(() => {
    announce();
    applyMessengerTheme();
    if (surface() === "telegram") void loadTelegramSdk().then(announce);
    if (tokenRef.current) return;
    // Opened from Telegram or MAX: the messenger's signed payload is the sign-in.
    signInFromWebApp()
      .then((key) => {
        if (key) setToken(key);
      })
      .catch((e) => setError(e instanceof Error ? e.message : tRef.current("The messenger did not confirm the sign-in.")));
  }, []);

  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => {
      try {
        const [menu, who, list] = await Promise.all([models(), me(), catalog().catch(() => [])]);
        if (!alive) return;
        setList(menu);
        setChats(list);
        setModel((current) => current || menu[0]?.id || "");
        const accountRow = isRecord(who) && isRecord(who.account) ? who.account : {};
        setAccount(String(accountRow.hint || accountRow.email || ""));
        setError("");
      } catch (e) {
        if (alive) setError(e instanceof CloudError ? e.message : tRef.current("Could not read the model list."));
      }
    })();
    return () => {
      alive = false;
    };
  }, [token]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, historyRows]);

  useEffect(() => {
    wireBackButton(() => {
      if (openChat) {
        setOpenChat(null);
        setHistoryRows([]);
        return true;
      }
      return false;
    });
  }, [openChat]);

  const visible = useMemo(() => turns.filter((turn) => turn.text || turn.reasoning || turn.pending), [turns]);

  /** What the model reads: the words of text files, pictures as pictures, the rest by name. */
  async function prepare(files: File[]): Promise<{ parts: CloudMessage["content"]; uploaded: UploadedFile[] }> {
    const uploaded: UploadedFile[] = [];
    const images: string[] = [];
    let text = "";
    for (const file of files) {
      if (isTextFile(file)) {
        const raw = (await file.text()).split("\u0000").join("");
        const body = raw.slice(0, TEXT_LIMIT);
        text += (text ? "\n\n" : "") + `--- ${file.name} ---\n${body}`;
        continue;
      }
      const saved = await uploadAttachment(file);
      uploaded.push(saved);
      if (isImageFile(file) && saved.url) images.push(saved.url);
      else text += (text ? "\n\n" : "") + `[вложение: ${file.name}]`;
    }
    const parts: CloudMessage["content"] = images.length
      ? [
          { type: "text" as const, text: text || "Посмотри приложенные изображения." },
          ...images.map((url) => ({ type: "image_url" as const, image_url: { url } })),
        ]
      : text;
    return { parts, uploaded };
  }

  async function openHistory(chatMeta: ConversationMeta) {
    setOpenChat(chatMeta);
    setHistoryBusy(true);
    setError("");
    try {
      const page = await history(chatMeta.cid, { limit: 100 });
      setHistoryRows(page.messages ?? []);
    } catch (e) {
      setError(e instanceof CloudError ? e.message : t("Could not read this conversation."));
      setHistoryRows([]);
    } finally {
      setHistoryBusy(false);
    }
  }

  async function send() {
    const text = draft.trim();
    if ((!text && pending.length === 0) || busy || !model) return;
    setDraft("");
    setError("");
    setBusy(true);

    let content: CloudMessage["content"] = text;
    let uploaded: UploadedFile[] = [];
    try {
      if (pending.length) {
        const prepared = await prepare(pending);
        content =
          typeof prepared.parts === "string" ? text + (prepared.parts ? "\n\n" + prepared.parts : "") : prepared.parts;
        uploaded = prepared.uploaded;
        setPending([]);
      }
    } catch (e) {
      setError(e instanceof CloudError ? e.message : t("Could not read this file."));
      setBusy(false);
      return;
    }

    const history: CloudMessage[] = [
      ...turns.filter((turn) => !turn.pending).map((turn) => ({ role: turn.role, content: turn.text })),
      { role: "user", content },
    ];
    setTurns((all) => [...all, { role: "user", text }, { role: "assistant", text: "", pending: true }]);

    // One conversation per screen session; its id is what every other client will see.
    if (!cidRef.current) cidRef.current = crypto.randomUUID();
    if (!startedRef.current) startedRef.current = Math.floor(Date.now() / 1000);
    const cid = cidRef.current;
    const sentAt = Math.floor(Date.now() / 1000);
    const title = (text || pending.map((file) => file.name).join(", ")).slice(0, 52);
    try {
      await publish(
        { cid, kind: "side", title, created_at: startedRef.current, updated_at: sentAt, ...(model ? { model } : {}) },
        [
          {
            mid: crypto.randomUUID(),
            cid,
            role: "user",
            text: typeof content === "string" ? content : text,
            created_at: sentAt,
            ...(uploaded.length
              ? { attachments: uploaded.map((f) => ({ name: f.name, mime: f.mime, size: f.size, url: f.url })) }
              : {}),
          },
        ],
      );
    } catch {
      setError(tRef.current("Could not save this message to the shared history."));
    }

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
      if (result.text) {
        const repliedAt = Math.floor(Date.now() / 1000);
        try {
          await publish(
            { cid, kind: "side", title, created_at: startedRef.current, updated_at: repliedAt, ...(model ? { model } : {}) },
            [{ mid: crypto.randomUUID(), cid, role: "assistant", text: result.text, created_at: repliedAt }],
          );
        } catch {
          setError(tRef.current("Could not save this message to the shared history."));
        }
      }
    } catch (e) {
      if ((e as Error)?.name === "AbortError") {
        setTurns((all) => {
          const next = [...all];
          const last = next[next.length - 1];
          if (last?.role === "assistant") next[next.length - 1] = { ...last, pending: false };
          return next;
        });
      } else {
        setError(e instanceof CloudError ? e.message : t("No answer came."));
        setTurns((all) => all.filter((turn) => !turn.pending));
      }
    } finally {
      abort.current = null;
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-3 bg-bg p-6">
        <img src="/avatars/lika-idle.webp" alt="" className="mx-auto h-32 w-32 rounded-full object-contain" />
        <h1 className="text-center text-lg font-semibold">{t("nanoLika Cloud")}</h1>
        <p className="text-center text-sm text-muted">
          {t("Paste the account key — the same nanoLika API key as in your account page.")}
        </p>
        <input
          type="password"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="nm_…"
          className="rounded-2xl border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button
          type="button"
          className="rounded-2xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg disabled:opacity-50"
          disabled={!draft.trim()}
          onClick={() => {
            setCloudToken(draft.trim());
            setToken(draft.trim());
            setDraft("");
          }}
        >
          {t("Sign in")}
        </button>
        <p className="text-center text-xs text-muted">{t("Relay: {url}", { url: relayBase() })}</p>
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col bg-bg">
      <header className="sticky top-0 z-10 flex items-center gap-3 border-b border-border/60 bg-bg/95 px-4 py-3 backdrop-blur">
        <img src="/avatars/lika-idle.webp" alt="" className="h-10 w-10 rounded-full object-contain" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">nanoLika Cloud</p>
          <p className="truncate text-xs text-muted">{account || relayBase()}</p>
        </div>
        <button
          type="button"
          className="rounded-full border border-border px-3 py-1.5 text-xs"
          onClick={() => {
            if (openChat) {
              setOpenChat(null);
              setHistoryRows([]);
              return;
            }
            const first = chats[0];
            if (first) void openHistory(first);
          }}
        >
          {openChat ? t("Back") : t("History")}
        </button>
        <select
          value={model}
          onChange={(event) => setModel(event.target.value)}
          className="max-w-[42%] rounded-full border border-border bg-surface px-2 py-1.5 text-xs"
        >
          {list.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name || entry.id}
            </option>
          ))}
        </select>
      </header>

      <main className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {openChat ? (
          <>
            <p className="text-center text-xs text-muted">
              {openChat.title || t("Untitled chat")} · {t("read only for now")}
            </p>
            {historyBusy && <Loader2 className="mx-auto animate-spin text-muted" size={16} />}
            {historyRows.map((row) => (
              <div key={row.mid} className={row.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div
                  className={
                    row.role === "user"
                      ? "max-w-[85%] whitespace-pre-wrap rounded-3xl bg-bubble-user px-4 py-2.5 text-sm text-bubble-user-fg"
                      : "max-w-[85%] whitespace-pre-wrap rounded-3xl bg-surface px-4 py-2.5 text-sm shadow-sm"
                  }
                >
                  {row.text}
                </div>
              </div>
            ))}
          </>
        ) : visible.length === 0 ? (
          <div className="flex flex-col items-center gap-3 pt-10 text-center">
            <img src="/avatars/lika-idle.webp" alt="" className="h-40 w-40 object-contain" />
            <h2 className="text-2xl font-semibold">{t("What shall we take care of?")}</h2>
            <p className="max-w-sm text-sm text-muted">{t("Write the first message — it goes to the cloud.")}</p>
            <div className="mt-2 flex flex-wrap justify-center gap-2">
              {[t("Help me write a letter"), t("Make a plan for the week"), t("Explain something in plain words")].map((idea) => (
                <button
                  key={idea}
                  type="button"
                  onClick={() => setDraft(idea)}
                  className="rounded-2xl border border-border bg-surface px-3 py-2 text-sm"
                >
                  {idea}
                </button>
              ))}
            </div>
          </div>
        ) : (
          visible.map((turn, index) => (
            <div key={index} className={turn.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={
                  turn.role === "user"
                    ? "max-w-[85%] whitespace-pre-wrap rounded-3xl bg-bubble-user px-4 py-2.5 text-sm text-bubble-user-fg"
                    : "max-w-[85%] whitespace-pre-wrap rounded-3xl bg-surface px-4 py-2.5 text-sm shadow-sm"
                }
              >
                {turn.reasoning && <p className="mb-1 text-xs italic text-muted">{turn.reasoning}</p>}
                {turn.text || (turn.pending ? <Loader2 className="animate-spin" size={14} /> : null)}
              </div>
            </div>
          ))
        )}
        {error && (
          <p className="rounded-2xl bg-amber-500/12 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">{error}</p>
        )}
        <div ref={bottom} />
      </main>

      {!openChat && (
        <>
          {pending.length > 0 && (
            <div className="flex flex-wrap gap-2 border-t border-border/60 px-4 pt-2 text-xs">
              {pending.map((file, index) => (
                <span key={index} className="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-1">
                  {file.name}
                  <button
                    type="button"
                    aria-label={t("Remove attachment")}
                    onClick={() => setPending((all) => all.filter((_, i) => i !== index))}
                    className="text-muted"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
          <form
            className="sticky bottom-0 flex items-end gap-2 border-t border-border/60 bg-bg/95 px-4 py-3 backdrop-blur"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              onChange={(event) => {
                const chosen = [...(event.target.files ?? [])];
                event.target.value = "";
                if (chosen.length) setPending((all) => [...all, ...chosen].slice(0, 4));
              }}
            />
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full border border-border text-muted"
              aria-label={t("Attach a file")}
              disabled={busy}
            >
              <Plus size={18} />
            </button>
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
              placeholder={t("Write to Lika…")}
              className="max-h-40 min-h-10 flex-1 resize-none rounded-3xl border border-border bg-surface px-4 py-2.5 text-sm outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={busy || (!draft.trim() && pending.length === 0)}
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-accent text-accent-fg disabled:opacity-40"
              aria-label={t("Send")}
            >
              {busy ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
