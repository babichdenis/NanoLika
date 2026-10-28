import { describe, expect, it } from "vitest";
import { inChatScope } from "./chat-scope";
import type { ThreadMeta } from "./types";
const row = (patch: Partial<ThreadMeta>) => ({ id: "chat", ...patch }) as ThreadMeta;
describe("chat list scopes", () => {
  it("keeps shared archived chats out of active and local lists", () => {
    const chat = row({ sync_cid: "cloud", archived: true, local_only: false });
    expect(inChatScope(chat, "active")).toBe(false);
    expect(inChatScope(chat, "local")).toBe(false);
    expect(inChatScope(chat, "archive")).toBe(true);
  });
  it("shows local history only under Local chats", () => {
    expect(inChatScope(row({ local_only: true }), "active")).toBe(false);
    expect(inChatScope(row({ local_only: true }), "local")).toBe(true);
  });
  it("keeps active shared chats visible and supports older servers", () => {
    expect(inChatScope(row({ sync_cid: "cloud" }), "active")).toBe(true);
    expect(inChatScope(row({}), "active")).toBe(true);
  });
});
