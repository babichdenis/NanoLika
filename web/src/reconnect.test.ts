import { describe, expect, it } from "vitest";
import { advanceConnection } from "./reconnect";

describe("chat history after a lost connection", () => {
  it("reloads once on recovery, including repeated outages", () => {
    let state = { connected: false, seen: false };
    const reloads = [false, true, true, false, false, true, true, false, true].map((connected) => {
      const next = advanceConnection(state, connected);
      state = next;
      return next.recovered;
    });
    expect(reloads).toEqual([false, false, false, false, false, true, false, false, true]);
  });
});
