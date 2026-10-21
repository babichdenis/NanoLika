import { describe, expect, it } from "vitest";

import { money } from "./money";

// the live relay: ¥ → $ at 7.1, $ → ₽ at the operator's 100, 6% on top
const relay = { usd_cny: 7.1, rub_per_usd: 100, topup_fee_pct: 6 };

describe("money", () => {
  it("prints the operator's rubles when he prices in them", () => {
    const cur = money(relay);
    expect(cur.symbol).toBe("₽");
    expect(cur.text(3.3493)).toBe("₽50.00");
    expect(cur.text(0.0064, 3)).toBe("₽0.096");
  });

  it("rewrites a translated line instead of leaving a yuan sign in it", () => {
    const cur = money(relay);
    expect(cur.s("+¥5 for each friend")).toBe("+₽5 for each friend");
  });

  it("keeps yuan when the operator does not price in rubles", () => {
    const cur = money({ usd_cny: 7.1, rub_per_usd: 0, topup_fee_pct: 6 });
    expect(cur.symbol).toBe("¥");
    expect(cur.text(10)).toBe("¥10.00");
    expect(cur.s("¥{amount} added")).toBe("¥{amount} added");
  });

  it("keeps yuan when there is no config at all", () => {
    expect(money(undefined).text(1)).toBe("¥1.00");
    expect(money({}).rub).toBe(false);
  });
});
