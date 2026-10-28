import { describe, expect, it } from "vitest";
import { DEFAULT_TOPUP_URL, fundsView, safeTopupUrl } from "../../cloud/nanomuse_cloud/web_client/funds.js";

const error = { code: "insufficient_funds", required_rub: 10.26, balance_rub: 5, currency: "RUB", topup_url: "https://relay.example/app" };
describe("priced generation refusal shared by both web clients", () => {
  it("uses structured prices and the relay's account link", () => {
    const view = fundsView({ funds: error }, "en")!;
    expect(view.message).toContain("₽10.26");
    expect(view.message).toContain("₽5.00");
    expect(view.topupUrl).toBe(error.topup_url);
    expect(fundsView({ details: error }, "ru")!.message).toContain("₽10,26");
    expect(fundsView({ error }, "zh-CN")!.topupLabel).toBe("充值");
  });
  it("retains a zero balance and never invents amounts for malformed fields", () => {
    expect(fundsView({ ...error, balance_rub: 0 })!.message).toContain("₽0.00");
    for (const value of [undefined, "5", true, -1, NaN, Infinity]) {
      const view = fundsView({ ...error, balance_rub: value })!;
      expect(view.message).not.toContain("₽");
    }
    expect(fundsView({ ...error, currency: "USD" })!.message).not.toContain("₽");
    expect(fundsView({ code: "out_of_tokens" })).toBeNull();
  });
  it("rejects executable and credential-bearing top-up links", () => {
    for (const url of ["javascript:alert(1)", "data:text/html,bad", "file:///tmp/x", "//other.example/app", "https://user:pass@relay.example/app", "http://relay.example/app", ""]) {
      expect(safeTopupUrl(url)).toBe(DEFAULT_TOPUP_URL);
    }
    expect(safeTopupUrl("/app")).toBe(DEFAULT_TOPUP_URL);
    expect(safeTopupUrl("http://localhost:8787/app")).toBe("http://localhost:8787/app");
  });
});
