import { describe, expect, it, vi } from "vitest";
import { PendingHistory, type PendingEntry } from "./pending-history";
import type { ConversationMeta, MessagePage, OutgoingChat, OutgoingMessage, PublishResult } from "./sync";

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() { return this.values.size; }
  clear() { this.values.clear(); }
  getItem(key: string) { return this.values.get(key) ?? null; }
  key(index: number) { return [...this.values.keys()][index] ?? null; }
  removeItem(key: string) { this.values.delete(key); }
  setItem(key: string, value: string) { this.values.set(key, String(value)); }
}

function entry(owner = "account-a"): PendingEntry {
  return {
    owner,
    chat: { cid: "chat-1", title: "Chat", kind: "side", created_at: 1, updated_at: 1 },
    message: { mid: "message-1", cid: "chat-1", role: "user", text: "Keep this", created_at: 1 },
    locallyCreated: true,
    attempted: false,
    safeRetry: false,
  };
}

function port() {
  let conversations: ConversationMeta[] = [];
  let deleted: ConversationMeta[] = [];
  let behaviour: (chat: OutgoingChat, messages: OutgoingMessage[]) => Promise<PublishResult> = async () => ({ rejected: [] });
  let rows: MessagePage = { messages: [] };
  const publish = vi.fn((chat: OutgoingChat, messages: OutgoingMessage[]) => behaviour(chat, messages));
  return {
    snapshot: async () => ({ conversations, deleted, account_id: "account-a" }),
    publish,
    history: async () => rows,
    setCatalog: (value: ConversationMeta[], removed: ConversationMeta[] = []) => { conversations = value; deleted = removed; },
    setPublish: (fn: typeof behaviour) => { behaviour = fn; },
    setHistory: (value: MessagePage) => { rows = value; },
  };
}

describe("pending cloud history", () => {
  it("keeps a history-limit refusal across restart and retries the same message id", async () => {
    const storage = new MemoryStorage(), api = port(), first = new PendingHistory(storage, api);
    first.add(entry());
    api.setPublish(async () => { throw Object.assign(new Error("full"), { status: 409, code: "message_limit" }); });
    expect(await first.flush("account-a", () => true)).toEqual({ remaining: 1, reason: "message_limit" });
    expect(first.list("account-a")[0].message.text).toBe("Keep this");
    const second = new PendingHistory(storage, api);
    api.setPublish(async () => ({ rejected: [] }));
    expect(await second.flush("account-a", () => true)).toEqual({ remaining: 0, reason: "" });
    expect(api.publish.mock.calls.map((call) => call[1][0].mid)).toEqual(["message-1", "message-1"]);
  });

  it("does not recreate a removed or archived conversation", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    pending.add(entry());
    api.setCatalog([], [{ cid: "chat-1" }]);
    expect((await pending.flush("account-a", () => true)).reason).toBe("conversation_deleted");
    api.setCatalog([{ cid: "chat-1", archived: true }]);
    expect((await pending.flush("account-a", () => true)).reason).toBe("conversation_archived");
    expect(api.publish).not.toHaveBeenCalled();
    expect(pending.list("account-a")[0].message.text).toBe("Keep this");
  });

  it("does not recreate a chat after an uncertain first write", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    pending.add(entry());api.setPublish(async () => { throw new Error("offline"); });
    expect((await pending.flush("account-a", () => true)).remaining).toBe(1);
    expect((await pending.flush("account-a", () => true)).reason).toBe("creation_unknown");
    expect(api.publish).toHaveBeenCalledTimes(1);
  });

  it("only accepts known_mid after verifying the same text in the same account and chat", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    api.setCatalog([{ cid: "chat-1" }]);
    api.setPublish(async () => ({ rejected: [{ reason: "known_mid", mid: "message-1" }] }));
    api.setHistory({ account_id: "account-a", messages: [{ mid: "message-1", cid: "chat-1", role: "user", text: "wrong", seq: 1, created_at: 1 }] });
    pending.add(entry());expect((await pending.flush("account-a", () => true)).remaining).toBe(1);
    api.setHistory({ account_id: "account-a", messages: [{ mid: "message-1", cid: "chat-1", role: "user", text: "Keep this", seq: 1, created_at: 1 }] });
    expect((await pending.flush("account-a", () => true)).remaining).toBe(0);
  });

  it("does not send or erase another account's messages and fails closed when storage is unavailable", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    pending.add(entry("account-b"));
    expect((await pending.flush("account-a", () => true)).remaining).toBe(0);
    expect(pending.list("account-b")).toHaveLength(1);expect(api.publish).not.toHaveBeenCalled();
    vi.spyOn(storage, "setItem").mockImplementation(() => { throw new Error("storage unavailable"); });
    expect(() => pending.add(entry())).toThrow();expect(api.publish).not.toHaveBeenCalled();
  });

  it("keeps the first account's pending text when the session changes during a request", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    api.setCatalog([{ cid: "chat-1" }]);pending.add(entry());
    let valid = true, release!: () => void, started!: () => void;
    const called = new Promise<void>((resolve) => { started = resolve; });
    api.setPublish(async () => { started();await new Promise<void>((resolve) => { release = resolve; });return { rejected: [] }; });
    const running = pending.flush("account-a", () => valid);
    await called;valid = false;release();await running;
    expect(pending.list("account-a")).toHaveLength(1);
    expect(pending.list("account-b")).toHaveLength(0);
    expect(api.publish).toHaveBeenCalledTimes(1);
  });

  it("keeps two users' queued messages separate on the same device", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    const a = entry(), b = entry("account-b");
    b.message.mid = "other-message";
    b.message.text = "Private to B";
    pending.add(a);pending.add(b);
    expect((await pending.flush("account-a", () => true)).remaining).toBe(0);
    expect(api.publish.mock.calls.map((call) => call[1][0].mid)).toEqual(["message-1"]);
    expect(pending.list("account-b")[0].message.text).toBe("Private to B");
  });

  it("keeps the order of turns created in the same second", async () => {
    const storage = new MemoryStorage(), api = port(), pending = new PendingHistory(storage, api);
    api.setCatalog([{ cid: "chat-1" }]);
    const first = entry(), second = entry();
    first.message.mid = "z-user";second.message.mid = "a-answer";second.message.role = "assistant";
    pending.add(first);pending.add(second);
    expect((await pending.flush("account-a", () => true)).remaining).toBe(0);
    expect(api.publish.mock.calls.map((call) => call[1][0].mid)).toEqual(["z-user", "a-answer"]);
  });
});
