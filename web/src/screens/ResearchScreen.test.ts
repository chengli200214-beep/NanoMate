import { createElement } from "react";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLocaleSetting } from "../i18n";
import { researchApi, type ResearchProjectDetail, type ResearchRun } from "../research-api";
import { ResearchScreen } from "./ResearchScreen";

const store = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock("../store", () => ({ useStore: () => store }));
vi.mock("../components/TabHeader", () => ({ TabHeader: ({ title }: { title: string }) => createElement("h1", null, title) }));

const project: ResearchProjectDetail = {
  id: "project-1", title: "A decision", spec_id: "spec-1", mode: "fixture", created_at: "2026-01-01T00:00:00Z",
  spec: { title: "A decision", goal: "Choose a runtime", candidates: ["https://github.com/example/one", "https://github.com/example/two"], constraints: [], mode: "fixture" },
  sources: [], runs: [], current_report: null,
};
let screen: ReactTestRenderer;
function text(node: ReactTestInstance): string { return node.children.map((child) => typeof child === "string" ? child : text(child)).join(""); }
function button(label: string): ReactTestInstance {
  const found = screen.root.findAllByType("button").find((node) => text(node).trim() === label);
  expect(found, `button: ${label}`).toBeDefined();
  return found!;
}
async function mount() { await act(async () => { screen = create(createElement(ResearchScreen)); }); }

beforeEach(() => {
  setLocaleSetting("en");
  vi.stubGlobal("window", { setTimeout, clearTimeout });
  vi.spyOn(researchApi, "projects").mockResolvedValue([]);
  vi.spyOn(researchApi, "project").mockResolvedValue(project);
  vi.spyOn(researchApi, "evidence").mockResolvedValue([]);
  vi.spyOn(researchApi, "reports").mockResolvedValue([]);
  vi.spyOn(researchApi, "changes").mockResolvedValue({ events: [], notifications: [], mute_rules: [] });
  store.toast.mockReset();
});
afterEach(() => {
  if (screen) act(() => screen.unmount());
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});

describe("research workbench", () => {
  it("creates an explicitly labelled fixture and starts the returned project", async () => {
    vi.spyOn(researchApi, "create").mockResolvedValue(project);
    vi.spyOn(researchApi, "start").mockResolvedValue({ id: "run-1", status: "queued" } as ResearchRun);
    await mount();
    await act(async () => { button("New project").props.onClick(); });
    const input = screen.root.findByType("input");
    const areas = screen.root.findAllByType("textarea");
    await act(async () => {
      input.props.onChange({ target: { value: "A decision" } });
      areas[0].props.onChange({ target: { value: "Choose a runtime" } });
      areas[1].props.onChange({ target: { value: project.spec.candidates.join("\n") } });
      screen.root.findByType("select").props.onChange({ target: { value: "fixture" } });
    });
    await act(async () => { screen.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
    expect(researchApi.create).toHaveBeenCalledWith(project.spec);
    const rendered = JSON.stringify(screen.toJSON());
    expect(rendered).toContain("Saved public sources and scripted responses. This is not live model verification.");
    expect(rendered).toContain("Start research to collect evidence and publish the first report.");
    await act(async () => { button("Start research").props.onClick(); });
    expect(researchApi.start).toHaveBeenCalledWith("project-1", "research", expect.any(String));
  });

  it("preserves an edited draft while active progress refreshes", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { setTimeout, clearTimeout });
    const running = { ...project, runs: [{ id: "run-1", project_id: project.id, spec_id: project.spec_id, status: "running", kind: "research", created_at: project.created_at }] };
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    vi.mocked(researchApi.project).mockResolvedValue(running);
    await mount();
    await act(async () => { button("Edit conditions").props.onClick(); });
    const title = screen.root.findByType("input");
    await act(async () => { title.props.onChange({ target: { value: "An unsaved decision" } }); });
    const before = vi.mocked(researchApi.project).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(vi.mocked(researchApi.project).mock.calls.length).toBe(before + 1);
    expect(screen.root.findByType("input").props.value).toBe("An unsaved decision");
  });

  it("shows a retry when the project list cannot load", async () => {
    vi.mocked(researchApi.projects).mockRejectedValueOnce(new Error("research unavailable"));
    await mount();
    expect(JSON.stringify(screen.toJSON())).toContain("research unavailable");
    await act(async () => { button("Try again").props.onClick(); });
    expect(researchApi.projects).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(screen.toJSON())).toContain("Start with a decision");
  });

  it("keeps one request key after a lost start response", async () => {
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    vi.spyOn(researchApi, "start").mockRejectedValueOnce(new Error("connection lost")).mockResolvedValueOnce({ id: "run-1", status: "queued" } as ResearchRun);
    await mount();
    await act(async () => { button("Start research").props.onClick(); });
    expect(store.toast).toHaveBeenCalledWith("connection lost");
    await act(async () => { button("Start research").props.onClick(); });
    const calls = vi.mocked(researchApi.start).mock.calls;
    expect(calls[0][2]).toBe(calls[1][2]);
  });
});
