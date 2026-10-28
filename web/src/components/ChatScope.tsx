import { Archive, ArchiveRestore } from "lucide-react";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { ThreadMeta } from "../types";

import type { ChatScope } from "../chat-scope";
export type { ChatScope } from "../chat-scope";
export { inChatScope } from "../chat-scope";
export function ChatScopeSelect({ value, onChange }: { value: ChatScope; onChange: (scope: ChatScope) => void }) {
  const t = useT();
  return <select aria-label={t("Chats")} value={value} onChange={(e) => onChange(e.target.value as ChatScope)} className="mx-3 my-2 rounded-lg border border-border bg-surface px-2 py-1.5 text-sm">
    <option value="active">{t("Active chats")}</option>
    <option value="archive">{t("Archive")}</option>
    <option value="local">{t("Local chats")}</option>
  </select>;
}
export function ChatArchiveButton({ thread }: { thread: ThreadMeta }) {
  const { dispatch, toast } = useStore();
  const t = useT();
  if (thread.id === "main") return null;
  const toggle = async () => {
    try {
      const row = await api.archiveThread(thread.id, !thread.archived);
      dispatch({ type: "ws", msg: { kind: "thread", thread: row } });
    } catch (e) { toast((e as Error).message); }
  };
  return <button type="button" disabled={thread.busy} aria-label={t(thread.archived ? "Restore from archive" : "Archive")} onClick={() => void toggle()} className="p-1.5 text-muted hover:text-fg disabled:opacity-40">
    {thread.archived ? <ArchiveRestore size={15} /> : <Archive size={15} />}
  </button>;
}
