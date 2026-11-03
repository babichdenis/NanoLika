import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { expect, it, vi } from "vitest";
import { ChatTune } from "./ChatTune";

const { patchThread } = vi.hoisted(() => ({ patchThread: vi.fn(async (id: string, patch: { temperature?: number | null }) => ({ id, title: "Chat", created_at: "", updated_at: "", busy: false, queued: 0, events: 0, temperature: patch.temperature ?? undefined })) }));
vi.mock("../api", () => ({ api: { patchThread } }));
// the sheet listens for Esc on window; the test renderer has no DOM to give it
vi.stubGlobal("window", { addEventListener() {}, removeEventListener() {} });

vi.mock("../i18n", () => ({ useT: () => (key: string, vars?: Record<string, string>) => (vars ? key.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "") : key) }));

const thread = (temperature?: number) => ({ id: "t1", title: "Chat", created_at: "", updated_at: "", busy: false, queued: 0, events: 0, temperature });

/** The slider is the only range input in the sheet. */
function slider(card: ReactTestRenderer) {
  return card.root.findAllByType("input").find((el) => el.props.type === "range")!;
}

it("shows the chat's own temperature and saves a new one", async () => {
  let card!: ReactTestRenderer;
  act(() => { card = create(createElement(ChatTune, { thread: thread(0.3), open: true, onClose: () => {} })); });
  expect(slider(card).props.value).toBe(0.3);
  expect(JSON.stringify(card.toJSON())).toContain("0.3");
  await act(async () => {
    slider(card).props.onChange({ target: { value: "1.4" } });
  });
  expect(slider(card).props.value).toBe(1.4);
  await act(async () => {
    slider(card).props.onPointerUp();
  });
  expect(patchThread).toHaveBeenCalledExactlyOnceWith("t1", { temperature: 1.4 });
  act(() => card.unmount());
});

it("a chat without its own value follows Settings, and Automatic puts it back", async () => {
  let card!: ReactTestRenderer;
  act(() => { card = create(createElement(ChatTune, { thread: thread(1.1), open: true, onClose: () => {} })); });
  const automatic = () => card.root.findAllByType("button").find((b) => b.props.children === "Automatic")!;
  await act(async () => { automatic().props.onClick(); });
  expect(patchThread).toHaveBeenLastCalledWith("t1", { temperature: null });
  expect(JSON.stringify(card.toJSON())).toContain("Follows the value in Settings.");
  act(() => card.unmount());
});
