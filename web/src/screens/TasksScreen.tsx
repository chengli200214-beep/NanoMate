import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../api";
import { useT } from "../i18n";
import { TabHeader } from "../components/TabHeader";
import { PersonalTaskComposer } from "../components/PersonalTaskComposer";
import { ExecutionTaskComposer } from "../components/ExecutionTaskComposer";
import { isAssistantDesktop } from "../desktop";
import { CheckCircle2, Clock3, ListTodo, Plus, X } from "lucide-react";

type StepSpec = { adapter: string; title: string; project_id?: string; kind?: string; preference?: { action: string; key: string; value?: string; purpose?: string } };
type Plan = { title: string; success_criteria: string; steps: StepSpec[] };
type Summary = { id: string; title: string; revision: number; status: string; due_at: number };
type Task = Summary & {
  plans: { revision: number; reason: string; spec: Plan }[];
  steps: { id: string; revision: number; status: string; spec: StepSpec; result: { [key: string]: unknown; run_id?: string; user_evidence?: string; domain_status?: string; error?: string; reminder?: { id: string; revision: number; status: string }; artifact?: { id: string; path: string }; preference?: { key: string; revision: number; status: string } } }[];
};
type Artifact = { id: string; path?: string; sha256?: string; hash?: string; verified?: boolean; content?: string; revision: number };
type Preference = { key: string; value: string; revision: number; status: string; purpose: string; source_task: string };
type Schedule = { id: string; enabled: number; next_at: number; payload: { plan: Plan; interval_seconds: number } };
const statuses: Record<string, string> = { queued: "Queued", running: "Running", bound: "Executing action", pending: "Pending", paused: "Paused", waiting_user: "Waiting for your confirmation", waiting_approval: "Waiting for approval", needs_attention: "Needs attention", outcome_unknown: "Result needs verification", completed: "Completed", cancelled: "Cancelled", cancelling: "Cancelling", superseded: "Superseded" };
const inputClass = "w-full rounded-xl border border-line bg-surface p-3 text-sm";
const buttonClass = "rounded-xl border border-line px-3 py-2 text-sm disabled:opacity-40";

export function TasksScreen() {
  const t = useT();
  const desktop = isAssistantDesktop();
  const [composer, setComposer] = useState<"personal" | "execution" | "plan" | null>(null);
  const [tasks, setTasks] = useState<Summary[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [preview, setPreview] = useState("");
  const [bindings, setBindings] = useState<{ id: string; title: string; module: string; status: string }[]>([]);
  const [projects, setProjects] = useState<{ id: string; title: string }[]>([]);
  const [selected, setSelected] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location?.search ?? "").get("task") ?? "");
  const [detail, setDetail] = useState<Task | null>(null);
  const [title, setTitle] = useState("");
  const [criteria, setCriteria] = useState("");
  const [steps, setSteps] = useState("");
  const [project, setProject] = useState("");
  const [due, setDue] = useState("");
  const [repeatMinutes, setRepeatMinutes] = useState("");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  // Preserve the request key after a lost response; changed form data gets a new key.
  const retry = useRef({ payload: "", key: "" });
  const mutationKeys = useRef(new Map<string, string>());
  const post = async <T,>(path: string, body: Record<string, unknown>): Promise<T> => {
    const signature = JSON.stringify([path, body]);
    const key = mutationKeys.current.get(signature) ?? crypto.randomUUID();
    mutationKeys.current.set(signature, key);
    const result = await request<T>(path, { method: "POST", body: JSON.stringify({ request_id: key, ...body }) });
    mutationKeys.current.delete(signature);
    return result;
  };
  const generation = useRef(0);
  const load = useCallback(async () => {
    const version = ++generation.current;
    const [rows, timers, modules, task, linked, prefs, outputs] = await Promise.all([
      request<Summary[]>("/api/assistant/tasks"), request<Schedule[]>("/api/assistant/schedules"),
      request<{ id: string; title: string }[]>("/api/research/projects"),
      selected ? request<Task>(`/api/assistant/tasks/${selected}`) : Promise.resolve(null),
      request<{ id: string; title: string; module: string; status: string }[]>("/api/assistant/module-bindings"),
      request<Preference[]>("/api/assistant/preferences"),
      selected ? request<Artifact[]>(`/api/assistant/tasks/${selected}/artifacts`) : Promise.resolve([]),
    ]);
    if (version !== generation.current) return;
    setTasks(rows); setSchedules(timers); setProjects(modules); setDetail(task); setBindings(linked); setPreferences(prefs); setArtifacts(outputs);
  }, [selected]);
  useEffect(() => {
    let active = true;
    const refresh = () => { if (active) void load().catch(() => { if (active) setError(true); }); };
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [load]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true); setError(false);
    try { await action(); await load(); } catch { setError(true); }
    finally { setBusy(false); }
  };
  const plan = (): Plan => ({ title: title.trim(), success_criteria: criteria.trim(), steps: [
    ...(project ? [{ adapter: "research" as const, title: t("Research"), project_id: project, kind: "research" }] : []),
    ...steps.split("\n").map((s) => s.trim()).filter(Boolean).map((s) => ({ adapter: "checkpoint" as const, title: s })),
  ] });
  const valid = Boolean(title.trim() && criteria.trim() && (project || steps.trim()));
  const create = () => run(async () => {
    const body = { plan: plan(), due_at: due ? new Date(due).getTime() / 1000 : 0,
      ...(repeatMinutes ? { interval_seconds: Number(repeatMinutes) * 60 } : {}) };
    const payload = JSON.stringify(body);
    if (retry.current.payload !== payload) retry.current = { payload, key: crypto.randomUUID() };
    await post(`/api/assistant/${repeatMinutes ? "schedules" : "tasks"}`, { ...body, request_id: retry.current.key });
    retry.current = { payload: "", key: "" }; setTitle(""); setCriteria(""); setSteps("");
  });
  const control = (action: string) => run(() => post(`/api/assistant/tasks/${selected}/actions`, { action, expected_revision: detail?.revision }));
  const current = detail?.steps.filter((s) => s.revision === detail.revision) ?? [];
  const terminal = detail && ["completed", "cancelled", "cancelling"].includes(detail.status);
  return <div className={desktop ? "assistant-task-page flex h-full flex-col" : "flex h-full flex-col"}>
    <TabHeader title={t("Task ledger")}>{desktop && <button className="assistant-primary-button" onClick={() => setComposer(composer ? null : "personal")}><Plus size={16} />{t("Create a task")}</button>}</TabHeader>
    <main className="overflow-auto p-4 space-y-5">
      <p className="text-sm text-muted">{desktop ? t("Your plans, progress and actions that need a decision.") : t("Tasks connect personal actions, files, browser and Windows apps. Research remains available as a module.")}</p>
      {desktop && <div className="assistant-task-stats"><div><ListTodo size={17} /><span>{t("All tasks")}</span><strong>{tasks.length}</strong></div><div><Clock3 size={17} /><span>{t("In progress")}</span><strong>{tasks.filter((task) => ["running", "queued", "bound"].includes(task.status)).length}</strong></div><div><CheckCircle2 size={17} /><span>{t("To review")}</span><strong>{tasks.filter((task) => ["needs_attention", "waiting_user", "waiting_approval", "outcome_unknown"].includes(task.status)).length}</strong></div></div>}
      {error && <p role="alert">{t("The request failed or the plan changed. Refresh and review before retrying.")}</p>}
      {!desktop && <button className={buttonClass} onClick={() => void run(load)} disabled={busy}>{t("Refresh")}</button>}
      {desktop && composer && <div className="assistant-task-compose-tabs">{([{ id: "personal", label: "Personal task" }, { id: "execution", label: "Desktop actions" }, { id: "plan", label: "Plan" }] as const).map((item) => <button key={item.id} aria-pressed={composer === item.id} onClick={() => setComposer(item.id)}>{t(item.label)}</button>)}<button className="assistant-icon" aria-label={t("Close")} onClick={() => setComposer(null)}><X size={15} /></button></div>}
      {(!desktop || composer === "personal") && <PersonalTaskComposer onCreated={load} />}
      {(!desktop || composer === "execution") && <ExecutionTaskComposer selected={detail} onCreated={load} />}
      {(!desktop || composer === "plan") && <section className="space-y-3 rounded-2xl border border-line p-4">
        <h2 className="font-medium">{t("Compose a plan")}</h2>
        <label className="block">{t("Task title")}<input className={inputClass} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500} /></label>
        <label className="block">{t("Success criteria")}<textarea className={inputClass} value={criteria} onChange={(e) => setCriteria(e.target.value)} maxLength={4000} /></label>
        <label className="block">{t("Research project (optional)")}<select className={inputClass} value={project} onChange={(e) => setProject(e.target.value)}><option value="">{t("No research step")}</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
        <label className="block">{t("Checkpoints, one per line")}<textarea className={inputClass} value={steps} onChange={(e) => setSteps(e.target.value)} /></label>
        <label className="block">{t("Start time (local, optional)")}<input className={inputClass} type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <label className="block">{t("Repeat every N minutes (optional)")}<input className={inputClass} type="number" min={1} max={525600} value={repeatMinutes} onChange={(e) => setRepeatMinutes(e.target.value)} /></label>
        <p className="text-sm text-muted">{t("Missed occurrences merge into one. An unfinished occurrence blocks the next. Repeats use elapsed time, not a daily wall clock.")}</p>
        <button className={buttonClass} disabled={busy || !valid || (Boolean(repeatMinutes) && (!due || Number(repeatMinutes) < 1))} onClick={() => void create()}>{t("Create task or schedule")}</button>
        {detail && !terminal && <>
          <label className="block">{t("Reason for replacing the remaining plan")}<input className={inputClass} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
          <p className="text-sm text-muted">{t("Replacement uses the form above. Completed evidence stays in history. Active research must settle first.")}</p>
          <button className={buttonClass} disabled={busy || !valid || !reason.trim() || current.some((s) => ["running", "bound"].includes(s.status))} onClick={() => void run(() => post(`/api/assistant/tasks/${selected}/plan-revisions`, { plan: plan(), expected_revision: detail.revision, reason }))}>{t("Replace selected task plan")}</button>
        </>}
      </section>}
      <section className="space-y-2"><h2 className="font-medium">{t("Tasks")}</h2>
        {!tasks.length && (desktop ? <div className="assistant-empty-tasks"><ListTodo size={29} strokeWidth={1.2} /><h3>{t("No work is waiting.")}</h3><p>{t("Start with a conversation, or create a task here.")}</p><button className="assistant-primary-button" onClick={() => setComposer("personal")}><Plus size={14} />{t("Create a task")}</button></div> : <p>{t("No tasks yet")}</p>)}
        {tasks.map((task) => <button key={task.id} aria-pressed={selected === task.id} className={`${buttonClass} block w-full text-left`} onClick={() => { setDetail(null); setSelected(task.id); setEvidence(""); setPreview(""); }}>{task.title} · {t(statuses[task.status] ?? task.status)} · {t("Revision")} {task.revision}</button>)}
      </section>
      {detail && detail.id === selected && <section className="space-y-3 rounded-2xl border border-line p-4">
        <h2 className="font-medium">{detail.title} · {t(statuses[detail.status] ?? detail.status)}</h2>
        <p>{detail.plans[detail.plans.length - 1]?.spec.success_criteria}</p>
        {!terminal && <div className="flex gap-2">
          <button className={buttonClass} disabled={busy} onClick={() => void control(detail.status === "paused" ? "resume" : "pause")}>{t(detail.status === "paused" ? "Resume" : "Pause")}</button>
          <button className={buttonClass} disabled={busy} onClick={() => void control("cancel")}>{t("Cancel")}</button>
          <button className={buttonClass} disabled={busy} onClick={() => void run(() => post(`/api/assistant/tasks/${selected}/take-over`, { expected_revision: detail.revision }))}>{t("Take over manually")}</button>
        </div>}
        <ol className="list-decimal pl-5 space-y-2">{current.map((step) => <li key={step.id}>{step.spec.title} · {t(statuses[step.status] ?? step.status)}
          {["file", "browser", "native"].includes(step.spec.adapter) && <details><summary>{t("Action preview and result")}</summary><pre className="overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({ intent: step.spec, result: step.result }, null, 2)}</pre></details>}
          {step.status === "waiting_user" && detail.status === "waiting_user" && ["file", "browser", "native"].includes(step.spec.adapter) && <button className={buttonClass} disabled={busy} onClick={() => void run(() => post(`/api/assistant/tasks/${selected}/steps/${step.id}/authorize-action`, { expected_revision: detail.revision }))}>{t("Approve this exact action")}</button>}
          {step.status === "outcome_unknown" && <p role="status">{t("The input may have reached its target. Take over, observe the current state and revise the remaining plan. Do not repeat the old input.")}</p>}
          {step.result.run_id && <p className="text-sm text-muted">{t("Research run")}: {step.result.run_id}</p>}
          {step.spec.project_id && <a className="text-sm underline" href={`/?tab=research&project=${encodeURIComponent(step.spec.project_id)}`}>{t("Open research project")}</a>}
          {step.status === "needs_attention" && <p role="status">{t("Review the current inputs and create a revised plan.")}</p>}
          {step.result.user_evidence && <p>{step.result.user_evidence}</p>}
          {step.result.reminder && <p className="text-sm">{t("Reminder configuration saved")}: {step.result.reminder.id} · {t("Revision")} {step.result.reminder.revision}</p>}
          {step.result.preference && <p className="text-sm">{t("Preference change recorded")}: {step.result.preference.key} · {t("Revision")} {step.result.preference.revision}</p>}
          {step.status === "waiting_user" && detail.status === "waiting_user" && step.spec.adapter === "preference" && <div className="space-y-2 rounded-xl border border-line p-3">
            <p>{t("Confirm this preference intent before it enters long-term memory.")}</p>
            <p>{step.spec.preference?.key}: {step.spec.preference?.value ?? t("Delete preference")}</p>
            <p>{step.spec.preference?.purpose}</p>
            <button className={buttonClass} disabled={busy} onClick={() => void run(() => post(`/api/assistant/tasks/${selected}/steps/${step.id}/authorize-preference`, { expected_revision: detail.revision }))}>{t("Confirm preference change")}</button>
          </div>}
          {step.status === "waiting_user" && detail.status === "waiting_user" && step.spec.adapter === "checkpoint" && <div className="space-y-2">
            <label className="block">{t("Completion evidence")}<textarea className={inputClass} value={evidence} onChange={(e) => setEvidence(e.target.value)} maxLength={4000} /></label>
            <button className={buttonClass} disabled={busy || !evidence.trim()} onClick={() => void run(async () => { await post(`/api/assistant/tasks/${selected}/steps/${step.id}/confirm`, { expected_revision: detail.revision, evidence }); setEvidence(""); })}>{t("Confirm checkpoint")}</button>
          </div>}
        </li>)}</ol>
        <details><summary>{t("Plan history")}</summary>{detail.plans.map((p) => <article key={p.revision} className="mt-3"><h3>{t("Revision")} {p.revision}: {p.spec.title}</h3><p>{p.reason === "Initial plan" ? t("Initial plan") : p.reason}</p><p>{p.spec.success_criteria}</p><ol>{detail.steps.filter((s) => s.revision === p.revision).map((s) => <li key={s.id}>{s.spec.title} · {t(statuses[s.status] ?? s.status)}{s.result.user_evidence && <p>{s.result.user_evidence}</p>}{s.result.artifact && <p>{s.result.artifact.path}</p>}</li>)}</ol></article>)}</details>
        <section className="space-y-2"><h3>{t("Task outputs")}</h3>{artifacts.map((a) => <div key={a.id} className="rounded-xl border border-line p-3 text-sm"><p>{a.path ?? a.id} · {t("Revision")} {a.revision}</p>{a.path && <><p>{t(a.verified ? "File verified" : "File changed or missing")}</p><button className={buttonClass} disabled={busy || !a.verified} onClick={() => void run(async () => { const output = await request<Artifact>(`/api/assistant/artifacts/${a.id}`); setPreview(output.content ?? ""); })}>{t("Read verified output")}</button></>}<p className="break-all text-xs text-muted">{a.sha256 ?? a.hash}</p></div>)}</section>
        {preview && <pre className="whitespace-pre-wrap break-words rounded-xl bg-surface-2 p-3 text-sm">{preview}</pre>}
      </section>}
      {(!desktop || schedules.length > 0) && <section className="space-y-2"><h2 className="font-medium">{t("Schedules")}</h2>{schedules.map((s) => <div key={s.id} className="rounded-xl border border-line p-3">{s.payload.plan.title} · {s.enabled ? new Date(s.next_at * 1000).toLocaleString() : t("Disabled")}
        {Boolean(s.enabled) && <button className={`${buttonClass} ml-3`} disabled={busy} onClick={() => void run(() => request(`/api/assistant/schedules/${s.id}`, { method: "DELETE" }))}>{t("Stop future occurrences")}</button>}
      </div>)}</section>}
      {(!desktop || bindings.length > 0) && <section className="space-y-2"><h2 className="font-medium">{t("Existing goals and reminders")}</h2>
        <p className="text-sm text-muted">{t("Read-only module view. A fired reminder does not prove its requested work was completed.")}</p>
        {bindings.map((b) => <p key={b.id}>{t(b.module === "goals" ? "Goals" : "Reminders")}: {b.title} · {t(({ active: "Active", done: "Done", paused: "Paused", cancelled: "Cancelled" } as Record<string, string>)[b.status] ?? b.status)}</p>)}
      </section>}
      {(!desktop || preferences.some((p) => p.status === "active")) && <section className="space-y-2"><h2 className="font-medium">{t("Confirmed personal preferences")}</h2><p className="text-sm text-muted">{t("Preferences are context. Your current request takes priority. Changes and deletion use a versioned task.")}</p>{preferences.filter((p) => p.status === "active").map((p) => <article key={p.key} className="rounded-xl border border-line p-3"><p>{p.key}: {p.value}</p><p className="text-sm text-muted">{p.purpose} · {t("Revision")} {p.revision}</p><button className={buttonClass} onClick={() => { setDetail(null); setSelected(p.source_task); setPreview(""); }}>{t("View preference source task")}</button></article>)}</section>}
    </main>
  </div>;
}
