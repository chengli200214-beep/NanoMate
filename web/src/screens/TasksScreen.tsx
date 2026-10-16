import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../api";
import { useT } from "../i18n";
import { TabHeader } from "../components/TabHeader";

type StepSpec = { adapter: "checkpoint" | "research"; title: string; project_id?: string; kind?: string };
type Plan = { title: string; success_criteria: string; steps: StepSpec[] };
type Summary = { id: string; title: string; revision: number; status: string; due_at: number };
type Task = Summary & {
  plans: { revision: number; reason: string; spec: Plan }[];
  steps: { id: string; revision: number; status: string; spec: StepSpec; result: { run_id?: string; user_evidence?: string; domain_status?: string; error?: string } }[];
};
type Schedule = { id: string; enabled: number; next_at: number; payload: { plan: Plan; interval_seconds: number } };
const statuses: Record<string, string> = { queued: "Queued", running: "Running", bound: "Research in progress", pending: "Pending", paused: "Paused", waiting_user: "Waiting for your confirmation", needs_attention: "Needs attention", completed: "Completed", cancelled: "Cancelled", cancelling: "Cancelling", superseded: "Superseded" };
const inputClass = "w-full rounded-xl border border-line bg-surface p-3 text-sm";
const buttonClass = "rounded-xl border border-line px-3 py-2 text-sm disabled:opacity-40";

export function TasksScreen() {
  const t = useT();
  const [tasks, setTasks] = useState<Summary[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [bindings, setBindings] = useState<{ id: string; title: string; module: string; status: string }[]>([]);
  const [projects, setProjects] = useState<{ id: string; title: string }[]>([]);
  const [selected, setSelected] = useState("");
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
    const [rows, timers, modules, task, linked] = await Promise.all([
      request<Summary[]>("/api/assistant/tasks"), request<Schedule[]>("/api/assistant/schedules"),
      request<{ id: string; title: string }[]>("/api/research/projects"),
      selected ? request<Task>(`/api/assistant/tasks/${selected}`) : Promise.resolve(null),
      request<{ id: string; title: string; module: string; status: string }[]>("/api/assistant/module-bindings"),
    ]);
    if (version !== generation.current) return;
    setTasks(rows); setSchedules(timers); setProjects(modules); setDetail(task); setBindings(linked);
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
  return <div className="flex h-full flex-col">
    <TabHeader title={t("Task ledger")} />
    <main className="overflow-auto p-4 space-y-5">
      <p className="text-sm text-muted">{t("Research runs automatically. Checkpoints wait for your evidence. Existing goals and reminders keep their own scheduler.")}</p>
      {error && <p role="alert">{t("The request failed or the plan changed. Refresh and review before retrying.")}</p>}
      <button className={buttonClass} onClick={() => void run(load)} disabled={busy}>{t("Refresh")}</button>
      <section className="space-y-3 rounded-2xl border border-line p-4">
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
      </section>
      <section className="space-y-2"><h2 className="font-medium">{t("Tasks")}</h2>
        {!tasks.length && <p>{t("No tasks yet")}</p>}
        {tasks.map((task) => <button key={task.id} aria-pressed={selected === task.id} className={`${buttonClass} block w-full text-left`} onClick={() => { setDetail(null); setSelected(task.id); setEvidence(""); }}>{task.title} · {t(statuses[task.status] ?? task.status)} · {t("Revision")} {task.revision}</button>)}
      </section>
      {detail && detail.id === selected && <section className="space-y-3 rounded-2xl border border-line p-4">
        <h2 className="font-medium">{detail.title} · {t(statuses[detail.status] ?? detail.status)}</h2>
        <p>{detail.plans[detail.plans.length - 1]?.spec.success_criteria}</p>
        {!terminal && <div className="flex gap-2">
          <button className={buttonClass} disabled={busy} onClick={() => void control(detail.status === "paused" ? "resume" : "pause")}>{t(detail.status === "paused" ? "Resume" : "Pause")}</button>
          <button className={buttonClass} disabled={busy} onClick={() => void control("cancel")}>{t("Cancel")}</button>
        </div>}
        <ol className="list-decimal pl-5 space-y-2">{current.map((step) => <li key={step.id}>{step.spec.title} · {t(statuses[step.status] ?? step.status)}
          {step.result.run_id && <p className="text-sm text-muted">{t("Research run")}: {step.result.run_id}</p>}
          {step.status === "needs_attention" && <p role="status">{t("Research needs attention. Review the project and create a revised plan.")}</p>}
          {step.result.user_evidence && <p>{step.result.user_evidence}</p>}
          {step.status === "waiting_user" && detail.status === "waiting_user" && <div className="space-y-2">
            <label className="block">{t("Completion evidence")}<textarea className={inputClass} value={evidence} onChange={(e) => setEvidence(e.target.value)} maxLength={4000} /></label>
            <button className={buttonClass} disabled={busy || !evidence.trim()} onClick={() => void run(async () => { await post(`/api/assistant/tasks/${selected}/steps/${step.id}/confirm`, { expected_revision: detail.revision, evidence }); setEvidence(""); })}>{t("Confirm checkpoint")}</button>
          </div>}
        </li>)}</ol>
        <details><summary>{t("Plan history")}</summary>{detail.plans.map((p) => <article key={p.revision} className="mt-3"><h3>{t("Revision")} {p.revision}: {p.spec.title}</h3><p>{p.reason === "Initial plan" ? t("Initial plan") : p.reason}</p><p>{p.spec.success_criteria}</p><ol>{p.spec.steps.map((s, i) => <li key={i}>{s.title}</li>)}</ol></article>)}</details>
      </section>}
      <section className="space-y-2"><h2 className="font-medium">{t("Schedules")}</h2>{schedules.map((s) => <div key={s.id} className="rounded-xl border border-line p-3">{s.payload.plan.title} · {s.enabled ? new Date(s.next_at * 1000).toLocaleString() : t("Disabled")}
        {Boolean(s.enabled) && <button className={`${buttonClass} ml-3`} disabled={busy} onClick={() => void run(() => request(`/api/assistant/schedules/${s.id}`, { method: "DELETE" }))}>{t("Stop future occurrences")}</button>}
      </div>)}</section>
      <section className="space-y-2"><h2 className="font-medium">{t("Existing goals and reminders")}</h2>
        <p className="text-sm text-muted">{t("Read-only module view. A fired reminder does not prove its requested work was completed.")}</p>
        {bindings.map((b) => <p key={b.id}>{t(b.module === "goals" ? "Goals" : "Reminders")}: {b.title} · {t(({ active: "Active", done: "Done", paused: "Paused", cancelled: "Cancelled" } as Record<string, string>)[b.status] ?? b.status)}</p>)}
      </section>
    </main>
  </div>;
}
