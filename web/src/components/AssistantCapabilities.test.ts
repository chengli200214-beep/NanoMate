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
