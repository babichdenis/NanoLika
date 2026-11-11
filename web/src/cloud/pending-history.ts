/** Persist unsynchronised web messages per account before sending them to the relay. */
import type { ConversationMeta, MessagePage, OutgoingChat, OutgoingMessage, PublishResult } from "./sync";

type Snapshot = { conversations: ConversationMeta[]; deleted: ConversationMeta[]; account_id?: string };
type Port = {
  snapshot: () => Promise<Snapshot>;
  publish: (chat: OutgoingChat, messages: OutgoingMessage[]) => Promise<PublishResult>;
  history: (cid: string, options: { before?: number; after?: number; limit?: number }) => Promise<MessagePage>;
};

export type PendingEntry = {
  owner: string;
  chat: OutgoingChat;
  message: OutgoingMessage;
  locallyCreated: boolean;
  attempted: boolean;
  safeRetry: boolean;
  queueOrder?: number;
};

export type FlushResult = { remaining: number; reason: string };
const PREFIX = "nl.cloud.pending.v1.";
const MAX_ENTRIES = 500;

export class PendingHistory {
  private running: Promise<FlushResult> | null = null;
  private lastOrder = 0;

  constructor(private readonly storage: Storage, private readonly port: Port) {}

  private prefix(owner: string): string {
    if (!owner) throw new Error("account_required");
    return PREFIX + encodeURIComponent(owner) + ".";
  }

  private key(entry: PendingEntry): string {
    return this.prefix(entry.owner) + encodeURIComponent(entry.message.mid);
  }

  list(owner: string): PendingEntry[] {
    const prefix = this.prefix(owner);
    const items: PendingEntry[] = [];
    for (let i = 0; i < this.storage.length; i++) {
      const key = this.storage.key(i);
      if (!key?.startsWith(prefix)) continue;
      const raw = this.storage.getItem(key);
      if (!raw) continue;
      const value: unknown = JSON.parse(raw);
      if (!value || typeof value !== "object") throw new Error("pending_storage_invalid");
      const entry = value as PendingEntry;
      if (entry.owner !== owner || !entry.chat?.cid || !entry.message?.mid || entry.message.cid !== entry.chat.cid || this.key(entry) !== key) {
        throw new Error("pending_storage_invalid");
      }
      items.push(entry);
    }
    return items.sort((a, b) => (a.queueOrder ?? a.message.created_at * 1000) - (b.queueOrder ?? b.message.created_at * 1000) || a.message.mid.localeCompare(b.message.mid));
  }

  add(entry: PendingEntry): void {
    const key = this.key(entry);
    const old = this.storage.getItem(key);
    const existing = this.list(entry.owner);
    if (!entry.queueOrder) {
      this.lastOrder = Math.max(Date.now(), this.lastOrder + 1, ...existing.map((item) => (item.queueOrder ?? 0) + 1));
      entry.queueOrder = this.lastOrder;
    }
    const raw = JSON.stringify(entry);
    if (old && old !== raw) throw new Error("pending_id_conflict");
    if (!old && existing.length >= MAX_ENTRIES) throw new Error("pending_storage_full");
    this.storage.setItem(key, raw);
    if (this.storage.getItem(key) !== raw) throw new Error("pending_storage_unavailable");
  }

  flush(owner: string, valid: () => boolean): Promise<FlushResult> {
    if (this.running) return this.running.then(() => this.flush(owner, valid));
    const running = this.flushOnce(owner, valid);
    this.running = running.finally(() => { this.running = null; });
    return this.running;
  }

  private async verifyKnown(entry: PendingEntry, valid: () => boolean): Promise<boolean> {
    if (entry.message.attachments?.length || new TextEncoder().encode(entry.message.text).length > 16_384) return false;
    let before = 0;
    for (let i = 0; i < 70; i++) {
      const page = await this.port.history(entry.chat.cid, before ? { before, limit: 300 } : { after: 0, limit: 300 });
      if (!valid() || (page.account_id && page.account_id !== entry.owner)) return false;
      const found = page.messages.find((row) => row.mid === entry.message.mid);
      if (found) return found.cid === entry.chat.cid && found.role === entry.message.role && !found.deleted && !found.truncated && found.text === entry.message.text;
      if (!page.has_older || !page.messages.length) return false;
      const oldest = Math.min(...page.messages.map((row) => Number(row.seq) || 0));
      if (!oldest || oldest === before) return false;
      before = oldest;
    }
    return false;
  }

  private async flushOnce(owner: string, valid: () => boolean): Promise<FlushResult> {
    if (!valid()) return { remaining: this.list(owner).length, reason: "account_changed" };
    const entries = this.list(owner);
    if (!entries.length) return { remaining: 0, reason: "" };
    let snapshot: Snapshot;
    try { snapshot = await this.port.snapshot(); } catch { return { remaining: entries.length, reason: "sync_unavailable" }; }
    if (!valid() || snapshot.account_id !== owner) return { remaining: entries.length, reason: "account_changed" };
    const blocked = new Set<string>();
    let reason = "";
    for (const entry of entries) {
      if (!valid()) break;
      const cid = entry.chat.cid;
      if (blocked.has(cid)) continue;
      const meta = snapshot.conversations.find((row) => row.cid === cid);
      const deleted = snapshot.deleted.some((row) => row.cid === cid);
      if (deleted || (!meta && !entry.locallyCreated)) { blocked.add(cid); reason ||= "conversation_deleted"; continue; }
      if (meta?.archived) { blocked.add(cid); reason ||= "conversation_archived"; continue; }
      if (!meta && entry.attempted && !entry.safeRetry) { blocked.add(cid); reason ||= "creation_unknown"; continue; }
      const key = this.key(entry);
      const current = this.storage.getItem(key);
      if (!current) continue;
      entry.attempted = true;
      entry.safeRetry = false;
      this.storage.setItem(key, JSON.stringify(entry));
      try {
        // An existing conversation is never unarchived by a stale browser copy.
        const chat = { ...entry.chat };
        if (meta) delete chat.archived;
        const result = await this.port.publish(chat, [entry.message]);
        if (!valid()) break;
        const rejected = result.rejected ?? [];
        if (rejected.length) {
          if (rejected.length !== 1 || rejected[0].reason !== "known_mid" || rejected[0].mid !== entry.message.mid || !await this.verifyKnown(entry, valid)) {
            blocked.add(cid);reason ||= rejected[0]?.reason || "sync_unavailable";continue;
          }
        }
        if (valid() && this.storage.getItem(key) === JSON.stringify(entry)) this.storage.removeItem(key);
        if (!meta) snapshot.conversations.push({ cid });
      } catch (error) {
        if (!valid()) break;
        const code = typeof error === "object" && error !== null && "code" in error ? String(error.code) : "sync_unavailable";
        const status = typeof error === "object" && error !== null && "status" in error ? Number(error.status) : 0;
        if (status === 409 && code === "message_limit") {
          entry.safeRetry = true;
          this.storage.setItem(key, JSON.stringify(entry));
        }
        blocked.add(cid);reason ||= code;
      }
    }
    return { remaining: this.list(owner).length, reason };
  }
}
