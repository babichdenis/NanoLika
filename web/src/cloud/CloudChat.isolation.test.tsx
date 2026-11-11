import { afterEach, describe, expect, it, vi } from "vitest";
import { act, create, type ReactTestRenderer } from "react-test-renderer";

const state = vi.hoisted(() => ({ key: "key-a", history: vi.fn(), send: vi.fn() }));
vi.mock("../i18n", () => ({ useT: () => (s: string) => s }));
vi.mock("./client", () => ({
  cloudToken: () => state.key, setCloudToken: (v: string) => { state.key = v; },
  chat: state.send, CloudError: class extends Error {}, isRecord: (v: unknown) => !!v && typeof v === "object",
  me: async () => ({ account: { id: state.key, hint: state.key } }),
  models: async () => [{ id: "model" }], relayBase: () => "relay", uploadAttachment: vi.fn(),
}));
vi.mock("./sync", () => ({
  catalog: async () => [{ cid: state.key + "-chat", title: state.key + " conversation" }],
  catalogSnapshot: async () => { throw new Error("offline"); },
  messages: state.history, publish: vi.fn(),
}));
vi.mock("./webapp", () => ({ announce: vi.fn(), applyMessengerTheme: vi.fn(), loadTelegramSdk: vi.fn(),
  signInFromWebApp: vi.fn(), surface: () => "web", wireBackButton: vi.fn() }));

import { CloudChat } from "./CloudChat";
import { PendingHistory } from "./pending-history";

class MemoryStorage implements Storage {
  private rows = new Map<string,string>();
  get length() { return this.rows.size; }
  clear() { this.rows.clear(); }
  getItem(k: string) { return this.rows.get(k) ?? null; }
  key(i: number) { return [...this.rows.keys()][i] ?? null; }
  removeItem(k: string) { this.rows.delete(k); }
  setItem(k: string, v: string) { this.rows.set(k, String(v)); }
}

describe("web chat account isolation", () => {
  let view: ReactTestRenderer | undefined;
  afterEach(() => { if (view) act(() => view?.unmount());view = undefined;vi.unstubAllGlobals();state.history.mockReset();state.send.mockReset(); });

  it("updates the account prop and ignores a late history response from the previous account", async () => {
    state.key = "key-a";
    vi.stubGlobal("localStorage", new MemoryStorage());
    vi.stubGlobal("window", { setInterval, clearInterval, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    let finish!: (value: unknown) => void;
    state.history.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => { view = create(<CloudChat standalone={false} token="key-a" />); });
    const oldButton = view!.root.findAllByType("button").find((b) => b.findAllByType("span").some((s) => s.children.includes("key-a conversation")));
    expect(oldButton).toBeTruthy();
    await act(async () => { oldButton!.props.onClick(); });
    state.key = "key-b";
    await act(async () => { view!.update(<CloudChat standalone={false} token="key-b" />); });
    await act(async () => { finish({ messages: [{ mid: "m", cid: "key-a-chat", role: "user", text: "private A", seq: 1, created_at: 1 }] }); });
    const output = JSON.stringify(view!.toJSON());
    expect(output).toContain("key-b conversation");
    expect(output).not.toContain("private A");
    expect(output).not.toContain("key-a conversation");
  });

  it("keeps a reloaded unsynced conversation separate and does not send a new request without its context", async () => {
    state.key = "key-a";
    const storage = new MemoryStorage();
    const previous = new PendingHistory(storage, { snapshot: async () => ({ conversations: [], deleted: [] }), publish: async () => ({ rejected: [] }), history: async () => ({ messages: [] }) });
    previous.add({ owner: "key-a", chat: { cid: "old-chat", title: "Old chat", kind: "side", created_at: 1, updated_at: 1 },
      message: { mid: "m1", cid: "old-chat", role: "user", text: "saved draft", created_at: 1 }, locallyCreated: true, attempted: false, safeRetry: false });
    vi.stubGlobal("localStorage", storage);
    vi.stubGlobal("window", { setInterval, clearInterval, addEventListener: vi.fn(), removeEventListener: vi.fn() });
    await act(async () => { view = create(<CloudChat standalone={false} token="key-a" />); });
    await act(async () => { view!.root.findByType("textarea").props.onChange({ target: { value: "continue this" } }); });
    await act(async () => { view!.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
    expect(state.send).not.toHaveBeenCalled();
    expect(view!.root.findByType("textarea").props.value).toBe("continue this");
    expect(JSON.stringify(view!.toJSON())).toContain("Messages from another conversation are waiting to sync");
    expect(previous.list("key-a")[0].message.text).toBe("saved draft");
  });

  it("standalone switches accounts on storage events and hides old history including on sign-out", async () => {
    state.key = "key-a";
    vi.stubGlobal("localStorage", new MemoryStorage());
    const target = new EventTarget();
    vi.stubGlobal("window", { setInterval, clearInterval, addEventListener: target.addEventListener.bind(target), removeEventListener: target.removeEventListener.bind(target) });
    let finish!: (value: unknown) => void;
    state.history.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await act(async () => { view = create(<CloudChat />); });
    const oldButton = view!.root.findAllByType("button").find((b) => b.findAllByType("span").some((s) => s.children.includes("key-a conversation")));
    expect(oldButton).toBeTruthy();
    await act(async () => { oldButton!.props.onClick(); });
    state.key = "key-b";
    await act(async () => { target.dispatchEvent(new Event("storage")); });
    await act(async () => { finish({ messages: [{ mid: "m", cid: "key-a-chat", role: "user", text: "private A", seq: 1, created_at: 1 }] }); });
    let output = JSON.stringify(view!.toJSON());
    expect(output).toContain("key-b conversation");
    expect(output).not.toContain("private A");
    expect(output).not.toContain("key-a conversation");
    state.key = "";
    await act(async () => { target.dispatchEvent(new Event("storage")); });
    output = JSON.stringify(view!.toJSON());
    expect(output).toContain("Sign in");
    expect(output).not.toContain("key-b conversation");
  });
});
