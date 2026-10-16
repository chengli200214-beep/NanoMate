import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../api";
import { setLocaleSetting } from "../i18n";
import { AssistantCapabilities } from "./AssistantCapabilities";

vi.mock("../api", () => ({ request: vi.fn() }));
let screen: ReactTestRenderer;
beforeEach(() => { vi.resetAllMocks(); setLocaleSetting("en"); });
afterEach(() => { if (screen) act(() => screen.unmount()); });

it("labels offline and registered tools without claiming execution success", async () => {
  vi.mocked(request).mockResolvedValue({ mode: "offline", checks: [{ id: "computer", status: "registered" }] });
  await act(async () => { screen = create(createElement(AssistantCapabilities, { revision: 0 })); });
  const text = JSON.stringify(screen.toJSON());
  expect(text).toContain("Offline scripted responses");
  expect(text).toContain("Tool registered; execution not verified");
  expect(text).toContain("Computer control");
});

it("refreshes after connection changes and does not display stale success on failure", async () => {
  vi.mocked(request).mockResolvedValueOnce({ mode: "live", checks: [{ id: "chat", status: "configured" }] });
  await act(async () => { screen = create(createElement(AssistantCapabilities, { revision: 0 })); });
  vi.mocked(request).mockRejectedValueOnce(new Error("private error details"));
  await act(async () => { screen.update(createElement(AssistantCapabilities, { revision: 1 })); });
  const text = JSON.stringify(screen.toJSON());
  expect(text).toContain("Could not load assistant capabilities.");
  expect(text).not.toContain("Model configured");
  expect(text).not.toContain("private error details");
});

it("shows unresolved model reservations separately from known cost estimates", async () => {
  vi.mocked(request).mockResolvedValue({ mode: "offline", checks: [{ id: "usage_limits", status: "needs_budget_configuration" }], usage_limits: { configuration_required: true, charged_upper_cny: 0.25, unsettled_reserved_cny: 2.12992, unknown_requests: 1 } });
  await act(async () => { screen = create(createElement(AssistantCapabilities, { revision: 0 })); });
  const text = JSON.stringify(screen.toJSON());
  expect(text).toContain("Local model limits");
  expect(text).toContain("0.250000");
  expect(text).toContain("2.129920");
  expect(text).toContain("Requests needing reconciliation");
  expect(text).toContain("do not verify the provider bill");
});

it("does not describe a configured allowance as a verified model connection", async () => {
  vi.mocked(request).mockResolvedValue({ mode: "live", checks: [{ id: "usage_limits", status: "limits_configured" }] });
  await act(async () => { screen = create(createElement(AssistantCapabilities, { revision: 0 })); });
  const text = JSON.stringify(screen.toJSON());
  expect(text).toContain("Local limits configured");
  expect(text).not.toContain("Model configured");
});
