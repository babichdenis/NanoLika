import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

function storage(choice?: string) {
  const values = new Map(choice ? [["nanomuse_theme", choice]] : []);
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
  };
}

describe("paper appearance defaults", () => {
  it("uses light paper on first launch even on a dark system", async () => {
    vi.stubGlobal("localStorage", storage());
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: vi.fn() }));
    const theme = await import("./theme");
    expect(theme.getThemeSetting()).toBe("light");
    expect(theme.getTheme()).toBe("light");
  });
  it("retains a saved dark choice and persists a later choice", async () => {
    const saved = storage("dark");
    vi.stubGlobal("localStorage", saved);
    const theme = await import("./theme");
    expect(theme.getTheme()).toBe("dark");
    theme.setThemeSetting("light");
    expect(saved.getItem("nanomuse_theme")).toBe("light");
  });
  it("follows the system when explicitly selected", async () => {
    vi.stubGlobal("localStorage", storage("system"));
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: vi.fn() }));
    const theme = await import("./theme");
    expect(theme.getThemeSetting()).toBe("system");
    expect(theme.getTheme()).toBe("dark");
  });
});
