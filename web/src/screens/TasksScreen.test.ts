import { createElement } from "react";
import { act, create, type ReactTestRenderer, type ReactTestInstance } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../api";
import { setLocaleSetting } from "../i18n";
import { TasksScreen } from "./TasksScreen";

vi.mock("../api", () => ({ request: vi.fn() }));
vi.mock("../components/TabHeader", () => ({ TabHeader: ({ title }: { title: string }) => createElement("h1", null, title) }));
let screen: ReactTestRenderer;
const text = (node: ReactTestInstance): string => node.children.map((n) => typeof n === "string" ? n : text(n)).join("");
const button = (label: string) => screen.root.findAllByType("button").find((n) => text(n) === label)!;
beforeEach(() => {
  setLocaleSetting("en");
  vi.mocked(request).mockReset().mockResolvedValue([]);
});
afterEach(async () => { if (screen) await act(async () => screen.unmount()); });

it("keeps a stable request key across a lost creation response", async () => {
  await act(async () => { screen = create(createElement(TasksScreen)); });
  const inputs = screen.root.findAllByType("input");
  const areas = screen.root.findAllByType("textarea");
  await act(async () => {
    inputs[0].props.onChange({ target: { value: "Study" } });
    areas[0].props.onChange({ target: { value: "A written review" } });
    areas[1].props.onChange({ target: { value: "Read\nReview" } });
  });
  vi.mocked(request).mockImplementation(async (_path, init) => {
    if (init?.method === "POST") throw new Error("lost response");
    return [] as never;
  });
  await act(async () => { button("Create task or schedule").props.onClick(); });
  await act(async () => { button("Create task or schedule").props.onClick(); });
  const posts = vi.mocked(request).mock.calls.filter((c) => c[1]?.method === "POST");
  expect(posts).toHaveLength(2);
  expect(posts[0][1]?.body).toEqual(posts[1][1]?.body);
  expect(JSON.parse(String(posts[0][1]?.body)).plan.steps).toHaveLength(2);
  expect(screen.root.findAllByProps({ role: "alert" })).toHaveLength(1);
});

it("confirms only a current checkpoint and sends its revision with evidence", async () => {
  const plan = { title: "Study", success_criteria: "Review", steps: [{ adapter: "checkpoint", title: "Read" }] };
  const task = { id: "one", title: "Study", status: "waiting_user", revision: 2, due_at: 0,
    plans: [{ revision: 2, reason: "Updated", spec: plan }],
    steps: [{ id: "step", revision: 2, status: "waiting_user", spec: plan.steps[0], result: {} }] };
  vi.mocked(request).mockImplementation(async (path) => {
    if (path === "/api/assistant/tasks") return [task] as never;
    if (path === "/api/assistant/tasks/one") return task as never;
    return [] as never;
  });
  await act(async () => { screen = create(createElement(TasksScreen)); });
  await act(async () => { button("Study · Waiting for your confirmation · Revision 2").props.onClick(); });
  expect(button("Confirm checkpoint").props.disabled).toBe(true);
  const areas = screen.root.findAllByType("textarea");
  await act(async () => { areas[areas.length - 1].props.onChange({ target: { value: "Saved review" } }); });
  await act(async () => { button("Confirm checkpoint").props.onClick(); });
  expect(JSON.parse(String(vi.mocked(request).mock.calls.find((c) => c[0].endsWith("/confirm"))?.[1]?.body)))
    .toEqual({ expected_revision: 2, evidence: "Saved review", request_id: expect.any(String) });
});
