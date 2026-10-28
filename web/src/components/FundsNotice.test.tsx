import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { expect, it, vi } from "vitest";
import { FundsNotice } from "./FundsNotice";

const { setTab } = vi.hoisted(() => ({ setTab: vi.fn() }));
vi.mock("../store", () => ({ useStore: () => ({ setTab }) }));
vi.mock("../i18n", () => ({ useLocale: () => "en" }));

it("shows the priced refusal and only navigates when the user chooses", () => {
  let card!: ReactTestRenderer;
  act(() => { card = create(createElement(FundsNotice, { info: {
    code: "insufficient_funds", required_rub: 10.26, balance_rub: 0,
    currency: "RUB", topup_url: "https://relay.example/app",
  } })); });
  expect(JSON.stringify(card.toJSON())).toContain("₽10.26");
  expect(JSON.stringify(card.toJSON())).toContain("₽0.00");
  expect(setTab).not.toHaveBeenCalled();
  const link = card.root.findByType("a");
  expect(link.props.href).toBe("https://relay.example/app");
  expect(link.props.rel).toBe("noopener noreferrer");
  act(() => { card.root.findByType("button").props.onClick(); });
  expect(setTab).toHaveBeenCalledExactlyOnceWith("connections");
  act(() => card.unmount());
});
