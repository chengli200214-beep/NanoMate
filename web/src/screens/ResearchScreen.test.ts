import { createElement } from "react";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLocaleSetting } from "../i18n";
import { researchApi, type ResearchProjectDetail, type ResearchReport, type ResearchRun } from "../research-api";
import { ResearchScreen } from "./ResearchScreen";

const store = vi.hoisted(() => ({ toast: vi.fn(), state: { connected: true, researchUpdates: {} as Record<string, number> } }));
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
function field(label: string): ReactTestInstance {
  return screen.root.findAllByType("label").find((node) => text(node).startsWith(label))!;
}
async function mount() { await act(async () => { screen = create(createElement(ResearchScreen)); }); }

beforeEach(() => {
  setLocaleSetting("en");
  vi.stubGlobal("window", { setTimeout, clearTimeout });
  vi.spyOn(researchApi, "projects").mockResolvedValue([]);
  vi.spyOn(researchApi, "project").mockResolvedValue(project);
  vi.spyOn(researchApi, "evidence").mockResolvedValue([]);
  vi.spyOn(researchApi, "conflicts").mockResolvedValue([]);
  vi.spyOn(researchApi, "reports").mockResolvedValue([]);
  vi.spyOn(researchApi, "changes").mockResolvedValue({ events: [], notifications: [], mute_rules: [] });
  store.toast.mockReset();
  store.state = { connected: true, researchUpdates: {} };
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

  it("binds a new source to a registered candidate by default", async () => {
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    vi.spyOn(researchApi, "addSource").mockResolvedValue({ id: "source-1", kind: "document", candidate: project.spec.candidates[0], title: "Official documentation", url: "https://github.com/example/one", content: "" });
    await mount();
    await act(async () => { button("Add source").props.onClick(); });
    const candidate = field("Related repository").findByType("select");
    expect(candidate.props.value).toBe(project.spec.candidates[0]);
    expect(candidate.findAllByType("option").map((node) => node.props.value)).toEqual(project.spec.candidates);
    await act(async () => {
      field("Source title").findByType("input").props.onChange({ target: { value: "Official documentation" } });
      field("Official document URL").findByType("input").props.onChange({ target: { value: "https://github.com/example/one" } });
    });
    await act(async () => { screen.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
    expect(researchApi.addSource).toHaveBeenCalledWith(project.id, expect.objectContaining({ candidate: project.spec.candidates[0] }));
  });

  it("keeps dynamic staleness when the current report is selected explicitly", async () => {
    const current: ResearchReport = { id: "report-current", project_id: project.id, spec_id: "spec-1", markdown: "Current report", created_at: project.created_at, stale: false };
    const previous: ResearchReport = { ...current, id: "report-previous", markdown: "Previous report" };
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    vi.mocked(researchApi.project).mockResolvedValue({ ...project, current_report: { ...current, stale: true } });
    vi.mocked(researchApi.reports).mockResolvedValue([current, previous]);
    await mount();
    const picker = screen.root.findAllByType("select").find((node) => node.props["aria-label"] === "Report version")!;
    await act(async () => { picker.props.onChange({ target: { value: previous.id } }); });
    expect(JSON.stringify(screen.toJSON())).not.toContain("Conditions or evidence changed. Run research to update this report.");
    await act(async () => { picker.props.onChange({ target: { value: current.id } }); });
    expect(JSON.stringify(screen.toJSON())).toContain("Conditions or evidence changed. Run research to update this report.");
  });

  it("matches mute rules by source, impact, conditions and change type", async () => {
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    vi.mocked(researchApi.changes).mockResolvedValue({
      events: [
        { id: "event-related", source_id: "source-1", spec_id: "spec-1", impact_level: "related", change_summary: "Related update" },
        { id: "event-recommendation", source_id: "source-1", spec_id: "spec-1", impact_level: "recommendation", change_summary: "Recommendation update" },
      ],
      notifications: [],
      mute_rules: [{ id: "mute-source-1-recommendation", source_id: "source-1", impact_level: "recommendation", scope: { spec_id: "spec-1", change_type: "content", constraint_refs: [] }, enabled: true }],
    });
    vi.spyOn(researchApi, "changeAction").mockResolvedValue({ id: "event-related" });
    await mount();
    await act(async () => { screen.root.findByProps({ id: "research-tab-changes" }).props.onClick(); });
    const articles = screen.root.findAllByType("article");
    const related = articles.find((node) => text(node).includes("Related update"))!;
    const recommendation = articles.find((node) => text(node).includes("Recommendation update"))!;
    const mute = related.findAllByType("button").find((node) => text(node).trim() === "Mute this source change");
    expect(mute).toBeDefined();
    expect(recommendation.findAllByType("button").some((node) => text(node).trim() === "Unmute this source change")).toBe(true);
    expect(field("Model call limit per check").findByType("input").props.max).toBe(200);
    await act(async () => { mute!.props.onClick(); });
    expect(researchApi.changeAction).toHaveBeenCalledWith("event-related", "mute");
  });

  it("discovers background work while idle and refreshes a snapshot after reconnect", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("window", { setTimeout, clearTimeout });
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    await mount();
    const before = vi.mocked(researchApi.project).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(vi.mocked(researchApi.project).mock.calls.length).toBe(before + 1);
    store.state = { connected: false, researchUpdates: {} };
    await act(async () => { screen.update(createElement(ResearchScreen)); });
    const disconnected = vi.mocked(researchApi.project).mock.calls.length;
    store.state = { connected: true, researchUpdates: { [project.id]: 7 } };
    await act(async () => { screen.update(createElement(ResearchScreen)); });
    expect(vi.mocked(researchApi.project).mock.calls.length).toBe(disconnected + 1);
  });

  it("loads conflicts independently and displays them on the evidence page", async () => {
    vi.mocked(researchApi.projects).mockResolvedValue([project]);
    vi.mocked(researchApi.conflicts).mockResolvedValue([{
      id: "conflict-1", project_id: project.id, candidate: project.spec.candidates[0], dimension: "platform",
      evidence_refs: ["evidence-1", "evidence-2"], reason: "Saved sources disagree about platform support.",
      version_scope: "README v1 and source v2", status: "open", created_at: project.created_at,
      spec_id: project.spec_id, run_id: "run-1",
    }]);
    await mount();
    expect(researchApi.conflicts).toHaveBeenCalledWith(project.id);
    await act(async () => { screen.root.findByProps({ id: "research-tab-evidence" }).props.onClick(); });
    const rendered = JSON.stringify(screen.toJSON());
    expect(rendered).toContain("Saved sources disagree about platform support.");
    expect(rendered).toContain("README v1 and source v2");
    expect(rendered).toContain("evidence-1");
    expect(rendered).toContain("evidence-2");
  });
});
