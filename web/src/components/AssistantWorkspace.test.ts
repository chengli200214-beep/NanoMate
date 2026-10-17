import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";
import { isAssistantDesktop, desktopBridge } from "../desktop";
import { setLocaleSetting } from "../i18n";
import { AssistantWelcome } from "./AssistantWorkspace";

const store = vi.hoisted(() => ({ draft: vi.fn() }));
vi.mock("../store", () => ({ useStore: () => store }));
let screen: ReactTestRenderer | undefined;
afterEach(() => { if (screen) act(() => screen?.unmount()); screen = undefined; vi.unstubAllGlobals(); store.draft.mockReset(); });
describe("Assistant desktop entry", () => {
  it("previews the desktop without granting any window capabilities", () => {
    vi.stubGlobal("window", { location: { search: "?ui=desktop" } });
    expect(isAssistantDesktop()).toBe(true);
    expect(desktopBridge()).toBeNull();
  });
  it("leaves regular web and research entry points unchanged", () => {
    vi.stubGlobal("window", { location: { search: "?tab=research" } });
    expect(isAssistantDesktop()).toBe(false);
  });
  it("a starter writes an editable draft instead of sending a task", () => {
    setLocaleSetting("en");
    act(() => { screen = create(createElement(AssistantWelcome)); });
    expect(store.draft).not.toHaveBeenCalled();
    const starters = screen!.root.findAllByType("button");
    expect(starters).toHaveLength(3);
    act(() => starters[1].props.onClick());
    expect(store.draft).toHaveBeenCalledExactlyOnceWith("Help me organize workspace files. Inspect the file list first, then show me a plan before making changes.");
  });
});
