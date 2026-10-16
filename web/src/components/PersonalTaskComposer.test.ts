import { createElement } from "react";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { request } from "../api";
import { setLocaleSetting } from "../i18n";
import { PersonalTaskComposer } from "./PersonalTaskComposer";

vi.mock("../api", () => ({ request: vi.fn() }));
let screen: ReactTestRenderer;
const text = (node: ReactTestInstance): string => node.children.map((child) => typeof child === "string" ? child : text(child)).join("");
const button = () => screen.root.findAllByType("button").find((node) => text(node) === "Create personal task")!;
const field = (label: string) => screen.root.findAllByType("label").find((node) => node.children[0] === label)!.find((node) => ["input", "select", "textarea"].includes(String(node.type)));
const change = async (label: string, value: string) => { await act(async () => { field(label).props.onChange({ target: { value } }); }); };
const body = () => JSON.parse(String([...vi.mocked(request).mock.calls].reverse().find((call) => call[1]?.method === "POST")?.[1]?.body));

beforeEach(() => {
  setLocaleSetting("en");
  vi.mocked(request).mockReset().mockResolvedValue([]);
});
afterEach(async () => { if (screen) await act(async () => screen.unmount()); });

it("keeps the request ID and source scope when a document creation response is lost", async () => {
  const created = vi.fn();
  await act(async () => { screen = create(createElement(PersonalTaskComposer, { onCreated: created })); });
  await change("Personal task type", "document");
  await change("Personal task title", "Read source notes");
  await change("Workspace source paths, one per line", "notes/a.md\nnotes/b.txt");
  vi.mocked(request).mockImplementation(async (_path, init) => {
    if (init?.method === "POST") throw new Error("Private transport details");
    return [] as never;
  });
  expect(button().props.disabled).toBe(false);
  await act(async () => { button().props.onClick(); });
  await act(async () => { button().props.onClick(); });
  const posts = vi.mocked(request).mock.calls.filter((call) => call[1]?.method === "POST");
  expect(posts).toHaveLength(2);
  expect(posts[0][1]?.body).toEqual(posts[1][1]?.body);
  expect(body().plan.steps[0]).toEqual({ adapter: "document", title: "Read source notes", document: { source_paths: ["notes/a.md", "notes/b.txt"], output_name: "digest.md", notes: "" } });
  expect(body().confirm_preferences).toBeUndefined();
  expect(created).not.toHaveBeenCalled();
  expect(JSON.stringify(screen.toJSON())).not.toContain("Private transport details");
});

it("sends the selected reminder revision and preserves its time on a text edit", async () => {
  vi.mocked(request).mockImplementation(async (path) => path === "/api/reminders" ? [{ id: "reminder", text: "Read", revision: 7, status: "active", repeat: "daily 08:00", next_at: "" }] as never : [] as never);
  const created = vi.fn();
  await act(async () => { screen = create(createElement(PersonalTaskComposer, { onCreated: created })); });
  await change("Personal task title", "Edit reading reminder");
  await change("Reminder action", "update");
  await change("Existing reminder", "reminder");
  await change("Reminder text", "Read chapter two");
  await act(async () => { button().props.onClick(); });
  expect(body().plan.steps[0].reminder).toEqual({ action: "update", reminder_id: "reminder", expected_revision: 7, text: "Read chapter two" });
  expect(created).toHaveBeenCalledOnce();
});

it("requires explicit consent for preferences and clears it after a value change", async () => {
  vi.mocked(request).mockImplementation(async (path) => path === "/api/assistant/preferences" ? [{ key: "reply.language", value: "English", purpose: "Replies", revision: 3, status: "active" }] as never : [] as never);
  await act(async () => { screen = create(createElement(PersonalTaskComposer, { onCreated: vi.fn() })); });
  await change("Personal task type", "preference");
  await change("Personal task title", "Update reply language");
  await change("Existing preference (optional)", "reply.language");
  await change("Preference value", "Chinese");
  expect(button().props.disabled).toBe(true);
  const consent = () => screen.root.findByProps({ type: "checkbox" });
  await act(async () => { consent().props.onChange({ target: { checked: true } }); });
  expect(button().props.disabled).toBe(false);
  await change("Preference value", "Chinese with concise explanations");
  expect(consent().props.checked).toBe(false);
  expect(button().props.disabled).toBe(true);
  await act(async () => { consent().props.onChange({ target: { checked: true } }); });
  await act(async () => { button().props.onClick(); });
  expect(body().confirm_preferences).toBe(true);
  expect(body().plan.steps[0].preference).toEqual({ action: "set", key: "reply.language", value: "Chinese with concise explanations", purpose: "Replies", expected_revision: 3 });
});

it("converts a future local reminder time to an explicit UTC instant", async () => {
  await act(async () => { screen = create(createElement(PersonalTaskComposer, { onCreated: vi.fn() })); });
  await change("Personal task title", "Read tomorrow");
  await change("Reminder text", "Read one chapter");
  const future = new Date(Date.now() + 86400000);
  const local = new Date(future.getTime() - future.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  await change("Future reminder time (local)", local);
  await act(async () => { button().props.onClick(); });
  expect(body().plan.steps[0].reminder.at).toBe(new Date(local).toISOString());
  expect(body().plan.steps[0].reminder.at).toMatch(/Z$/);
});
