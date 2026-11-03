import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { expect, it, vi } from "vitest";
import { HandsLiveBar } from "./HandsLive";

const { stopHands } = vi.hoisted(() => ({ stopHands: vi.fn(async () => ({ stopped: true })) }));
vi.mock("../api", () => ({ api: { stopHands } }));
vi.mock("../i18n", () => ({
  useT: () => (key: string, vars?: Record<string, string>) =>
    vars ? key.replace(/\{(\w+)\}/g, (_, name) => vars[name] ?? "") : key,
}));

const live = (over: Record<string, unknown> = {}) => ({
  event: "act",
  thread: "t1",
  action: "click",
  label: "Send",
  app: "Mail",
  ts: 1,
  ...over,
});

it("shows the hands at work: the window and the last step, with a Stop that calls the runtime", async () => {
  let bar!: ReactTestRenderer;
  act(() => {
    bar = create(createElement(HandsLiveBar, { live: live(), name: "Lika" }));
  });
  const text = JSON.stringify(bar.toJSON());
  expect(text).toContain("Lika is using this computer");
  expect(text).toContain("Mail");
  expect(text).toContain("Clicked “Send”");
  await act(async () => {
    bar.root.findAllByType("button")[0].props.onClick();
  });
  expect(stopHands).toHaveBeenCalledOnce();
  act(() => bar.unmount());
});

it("draws nothing once the hands are gone", () => {
  let bar!: ReactTestRenderer;
  act(() => {
    bar = create(createElement(HandsLiveBar, { live: null, name: "Lika" }));
  });
  expect(bar.toJSON()).toBeNull();
  act(() => bar.unmount());
});
