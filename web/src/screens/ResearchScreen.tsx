import { Check, Download, ExternalLink, FlaskConical, Loader2, Plus, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { inputCls, secondaryBtn } from "../components/Form";
import { LoadError } from "../components/LoadError";
import { Markdown } from "../components/Markdown";
import { TabHeader } from "../components/TabHeader";
import { intlLocale, useT } from "../i18n";
import {
  isActiveRun, repositoryUrl, researchApi, runControls,
  type ResearchChanges, type ResearchConstraint, type ResearchDiagnostics, type ResearchDiff,
  type ResearchEvidence, type ResearchProject, type ResearchProjectDetail, type ResearchReport,
  type ResearchRun, type ResearchSpec, type ResearchTracking,
} from "../research-api";
import { useStore } from "../store";
import { cx } from "../util";

const primary = "inline-flex items-center justify-center gap-2 rounded-2xl bg-ink px-4 py-2.5 text-[14px] font-medium text-ink-fg disabled:opacity-40";
const small = "inline-flex items-center justify-center gap-1.5 rounded-xl border border-border px-3 py-2 text-[12.5px] font-medium hover:bg-surface-2 disabled:opacity-40";
type View = "overview" | "evidence" | "runs" | "changes";
type Perform = (key: string, action: () => Promise<unknown>, success?: string) => Promise<boolean>;
const viewLabels: Record<View, string> = { overview: "Overview", evidence: "Evidence", runs: "Runs", changes: "Changes" };

function Loading() { return <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-muted"><Loader2 size={16} className="animate-spin" /><span><LoadingLabel /></span></div>; }
function LoadingLabel() { const t = useT(); return t("Loading research…"); }
function Empty({ children }: { children: ReactNode }) { return <p className="py-6 text-[14px] leading-relaxed text-muted">{children}</p>; }
function Panel({ title, children, trailing }: { title: string; children: ReactNode; trailing?: ReactNode }) {
  return <section className="rounded-3xl border border-border bg-surface p-4 sm:p-5"><div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h3 className="text-[16px] font-semibold">{title}</h3>{trailing}</div>{children}</section>;
}
function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="block space-y-1.5 text-[13px] font-medium"><span>{label}</span>{children}{hint && <span className="block text-[12px] font-normal leading-relaxed text-muted">{hint}</span>}</label>;
}
function Stamp({ value }: { value?: string }) {
  if (!value) return null;
  const parsed = new Date(value);
  return <time dateTime={value} className="text-[12px] text-muted">{Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString(intlLocale())}</time>;
}
function Status({ value }: { value: string }) {
  const t = useT();
  const labels: Record<string, string> = {
    queued: "Queued", running: "Running", paused: "Paused", succeeded: "Succeeded", failed: "Failed", cancelled: "Cancelled", retry_wait: "Waiting to retry", recovering: "Recovering", pending: "Pending", stale: "Out of date", active: "Current", valid: "Current", superseded: "Superseded", corrected: "Corrected", needs_attention: "Needs attention", irrelevant: "Irrelevant", related: "Related", recommendation: "Recommendation affected", uncertain: "Uncertain",
  };
  return <span className={cx("inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium", value === "failed" || value === "stale" ? "bg-amber-500/10 text-amber-700 dark:text-amber-300" : "bg-surface-2 text-muted")}>{t(labels[value] ?? value)}</span>;
}
function SourceLink({ url, children }: { url: string; children?: ReactNode }) {
  let allowed = false;
  try { allowed = ["https:", "http:"].includes(new URL(url).protocol); } catch { /* invalid URLs stay text */ }
  return allowed ? <a href={url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-1 break-all text-[12px] text-accent hover:underline">{children || url}<ExternalLink size={12} className="shrink-0" /></a> : <span className="break-all text-[12px] text-muted">{children || url}</span>;
}

export function ResearchScreen() {
  const t = useT();
  const { toast } = useStore();
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [selected, setSelected] = useState("");
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const alive = useRef(true);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await researchApi.projects();
      if (!alive.current) return;
      setProjects(result);
      setSelected((old) => result.some((p) => p.id === old) ? old : result[0]?.id ?? "");
      setError("");
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { if (alive.current) setLoading(false); }
  }, []);
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; }; }, [refresh]);

  const create = async (spec: ResearchSpec) => {
    setSaving(true);
    try {
      const project = await researchApi.create(spec);
      setProjects((old) => [project, ...old]);
      setSelected(project.id);
      setCreating(false);
      toast(t("Research project created."));
      return true;
    } catch (e) { toast((e as Error).message); return false; }
    finally { setSaving(false); }
  };
  return (
    <div className="flex h-full min-w-0 flex-col">
      <TabHeader title={t("Research")} />
      <div className="flex-1 overflow-y-auto px-4 pb-8 sm:px-5">
        <p className="mb-5 max-w-xl text-[14px] leading-relaxed text-muted">{t("Compare repositories with evidence, keep reports current and resume interrupted work.")}</p>
        <div className="mb-5 flex flex-wrap items-end gap-2">
          {projects.length > 0 && <label className="min-w-0 flex-1 text-[12px] text-muted">{t("Research project")}<select className={cx(inputCls, "mt-1 text-fg")} value={selected} onChange={(e) => { setSelected(e.target.value); setCreating(false); }} aria-label={t("Research project")}>{projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>}
          <button type="button" className={small} onClick={() => setCreating(!creating)}><Plus size={15} />{t("New project")}</button>
          <button type="button" className={small} disabled={loading} onClick={() => void refresh()} aria-label={t("Refresh projects")}><RefreshCw size={15} className={loading ? "animate-spin" : ""} /></button>
        </div>
        {error && <LoadError message={error} onRetry={() => void refresh()} />}
        {loading && projects.length === 0 && <Loading />}
        {creating && <Panel title={t("Create a research project")}><SpecForm onSave={create} busy={saving} onCancel={() => setCreating(false)} /></Panel>}
        {!creating && !loading && !error && projects.length === 0 && <Panel title={t("Start with a decision")}><Empty>{t("Add two or three GitHub repositories, describe your goal and record the conditions that matter.")}</Empty><button type="button" className={primary} onClick={() => setCreating(true)}><Plus size={16} />{t("Create a research project")}</button></Panel>}
        {!creating && selected && <ProjectWorkspace key={selected} id={selected} onChanged={() => void refresh()} />}
      </div>
    </div>
  );
}

function ConstraintFields({ value, onChange, disabled }: { value: ResearchConstraint[]; onChange: (v: ResearchConstraint[]) => void; disabled: boolean }) {
  const t = useT();
  return <fieldset className="space-y-2" disabled={disabled}><legend className="mb-2 text-[13px] font-medium">{t("Decision conditions")}</legend>{value.map((item, index) => <div key={item.id} className="flex items-center gap-2"><input aria-label={t("Condition {n}", { n: index + 1 })} className={inputCls} value={item.text} maxLength={1000} onChange={(e) => onChange(value.map((v) => v.id === item.id ? { ...v, text: e.target.value } : v))} /><label className="flex shrink-0 items-center gap-1 text-[12px] text-muted"><input type="checkbox" checked={item.hard} onChange={(e) => onChange(value.map((v) => v.id === item.id ? { ...v, hard: e.target.checked } : v))} />{t("Required")}</label><button type="button" aria-label={t("Remove condition {n}", { n: index + 1 })} onClick={() => onChange(value.filter((v) => v.id !== item.id))} className="p-1 text-muted"><X size={16} /></button></div>)}<button type="button" className={small} disabled={value.length >= 30} onClick={() => onChange([...value, { id: crypto.randomUUID(), text: "", hard: true }])}><Plus size={14} />{t("Add condition")}</button></fieldset>;
}

function SpecForm({ initial, onSave, onCancel, busy }: { initial?: ResearchSpec; onSave: (spec: ResearchSpec) => Promise<boolean>; onCancel: () => void; busy: boolean }) {
  const t = useT();
  const [title, setTitle] = useState(initial?.title ?? "");
  const [goal, setGoal] = useState(initial?.goal ?? "");
  const [candidates, setCandidates] = useState(initial?.candidates.join("\n") ?? "");
  const [constraints, setConstraints] = useState(initial?.constraints ?? []);
  const [mode, setMode] = useState<ResearchSpec["mode"]>(initial?.mode ?? "live");
  const [error, setError] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const lines = candidates.split("\n").map((v) => v.trim()).filter(Boolean);
    const urls = lines.map(repositoryUrl);
    if (urls.length < 2 || urls.length > 3 || urls.some((v) => !v) || new Set(urls.map((v) => v?.toLowerCase())).size !== urls.length) { setError(t("Enter two or three different public GitHub repository URLs, one per line.")); return; }
    if (!title.trim() || !goal.trim()) return;
    setError("");
    void onSave({ title: title.trim(), goal: goal.trim(), candidates: urls as string[], constraints: constraints.filter((c) => c.text.trim()).map((c) => ({ ...c, text: c.text.trim() })), mode });
  };
  return <form onSubmit={submit} className="space-y-4"><fieldset disabled={busy} className="space-y-4">
    <Field label={t("Project title")}><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required /></Field>
    <Field label={t("Decision goal")}><textarea className={inputCls} value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={10000} rows={3} required /></Field>
    <Field label={t("GitHub repositories")} hint={t("Two or three public repository URLs, one per line.")}><textarea className={cx(inputCls, "font-mono text-[12px]")} value={candidates} onChange={(e) => setCandidates(e.target.value)} rows={3} required placeholder="https://github.com/owner/repository" /></Field>
    <ConstraintFields value={constraints} onChange={setConstraints} disabled={busy} />
    <Field label={t("Research mode")}><select className={inputCls} value={mode} onChange={(e) => setMode(e.target.value as ResearchSpec["mode"])} disabled={!!initial}><option value="live">{t("Live, fetch public sources and use the configured model")}</option><option value="fixture">{t("Fixture, use saved sources and scripted responses")}</option></select></Field>
    <p className="text-[12px] leading-relaxed text-muted">{mode === "fixture" ? t("Fixture results demonstrate the workflow. They do not verify the current state of a repository.") : t("Live research uses the runtime's configured model and public source access.")}</p>
  </fieldset>{error && <p role="alert" className="text-sm text-rose-600">{error}</p>}<div className="flex gap-2"><button type="submit" disabled={busy} className={primary}>{busy && <Loader2 size={14} className="animate-spin" />}{t(initial ? "Save revision" : "Create project")}</button><button type="button" disabled={busy} onClick={onCancel} className={secondaryBtn}>{t("Cancel")}</button></div></form>;
}

interface ProjectSnapshot { detail: ResearchProjectDetail; evidence: ResearchEvidence[]; reports: ResearchReport[]; changes: ResearchChanges }
function ProjectWorkspace({ id, onChanged }: { id: string; onChanged: () => void }) {
  const t = useT();
  const { toast } = useStore();
  const [data, setData] = useState<ProjectSnapshot | null>(null);
  const [view, setView] = useState<View>("overview");
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [pending, setPending] = useState("");
  const [editing, setEditing] = useState(false);
  const [diagnostics, setDiagnostics] = useState<ResearchDiagnostics | null>(null);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const alive = useRef(true);
  const requestId = useRef(0);
  const pendingRef = useRef(false);
  const startKeys = useRef<Record<string, string>>({});
  const refresh = useCallback(async () => {
    const seq = ++requestId.current;
    setRefreshing(true);
    try {
      const [detail, evidence, reports, changes] = await Promise.all([researchApi.project(id), researchApi.evidence(id), researchApi.reports(id), researchApi.changes(id)]);
      if (!alive.current || seq !== requestId.current) return;
      setData({ detail, evidence, reports, changes }); setError("");
    } catch (e) { if (alive.current && seq === requestId.current) setError((e as Error).message); }
    finally { if (alive.current && seq === requestId.current) setRefreshing(false); }
  }, [id]);
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; }; }, [refresh]);
  const active = data?.detail.runs.some(isActiveRun) ?? false;
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof window.setTimeout>;
    const tick = async () => {
      if (!pendingRef.current) await refresh();
      if (!cancelled) timer = window.setTimeout(() => void tick(), 2000);
    };
    timer = window.setTimeout(() => void tick(), 2000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [active, refresh]);
  const perform: Perform = async (key, action, success) => {
    if (pendingRef.current) return false;
    pendingRef.current = true; setPending(key);
    try {
      await action();
      if (success) toast(t(success));
      await refresh();
      return true;
    } catch (e) { toast((e as Error).message); return false; }
    finally { pendingRef.current = false; if (alive.current) setPending(""); }
  };
  const start = (kind: "research" | "check") => perform(kind, async () => {
    // Keep the key on a lost response, so retrying the click cannot create a second run.
    startKeys.current[kind] ??= crypto.randomUUID();
    await researchApi.start(id, kind, startKeys.current[kind]);
    delete startKeys.current[kind];
  }, "Run scheduled.");
  if (!data) return error ? <LoadError message={error} onRetry={() => void refresh()} /> : <Loading />;
  const { detail, evidence, reports, changes } = data;
  const busy = !!pending;
  const exportReport = (report: ResearchReport) => perform("export", async () => {
    const markdown = await researchApi.exportMarkdown(report.id);
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `${detail.title.replace(/[<>:"/\\|?*]/g, "_") || "research"}.md`; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  return <div className="space-y-4">
    {error && <LoadError message={error} onRetry={() => void refresh()} />}
    <section className="rounded-3xl bg-surface-2 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="min-w-0 break-words text-xl font-semibold">{detail.title}</h2><span className="flex items-center gap-1 rounded-full border border-border px-2 py-1 text-[11px]"><FlaskConical size={12} />{t(detail.mode === "fixture" ? "Fixture mode" : "Live mode")}</span></div>
      <p className="mt-2 whitespace-pre-wrap text-[14px] leading-relaxed">{detail.spec.goal}</p>
      <div className="mt-2 flex flex-col gap-1">{detail.spec.candidates.map((url) => <SourceLink key={url} url={url} />)}</div>
      {detail.mode === "fixture" && <p className="mt-3 text-[12px] text-muted">{t("Saved public sources and scripted responses. This is not live model verification.")}</p>}
      <div className="mt-4 flex flex-wrap gap-2"><button type="button" className={primary} disabled={busy || active} onClick={() => void start("research")}>{pending === "research" && <Loader2 size={14} className="animate-spin" />}{t(detail.current_report ? "Run research again" : "Start research")}</button><button type="button" className={small} disabled={busy || active || !detail.current_report} onClick={() => void start("check")}>{t("Check for changes")}</button><button type="button" className={small} disabled={busy} onClick={() => setEditing(!editing)}>{t("Edit conditions")}</button><button type="button" className={small} disabled={refreshing || busy} aria-label={t("Refresh project")} onClick={() => void refresh()}><RefreshCw size={14} className={refreshing ? "animate-spin" : ""} /></button></div>
      {active && <p role="status" className="mt-2 text-[12px] text-muted">{t("A run is active. Progress refreshes every two seconds.")}</p>}
    </section>
    {editing && <Panel title={t("Revise project conditions")}><SpecForm initial={detail.spec} busy={busy} onCancel={() => setEditing(false)} onSave={async (spec) => { const ok = await perform("revision", () => researchApi.revise(id, spec), "Revision saved. Affected conclusions will be rechecked."); if (ok) { setEditing(false); onChanged(); } return ok; }} /></Panel>}
    <nav aria-label={t("Research views")} className="flex gap-1 rounded-2xl bg-surface-2 p-1" role="tablist">{(Object.keys(viewLabels) as View[]).map((tab) => <button type="button" role="tab" id={`research-tab-${tab}`} aria-controls={`research-panel-${tab}`} aria-selected={view === tab} key={tab} onClick={() => setView(tab)} className={cx("min-w-0 flex-1 rounded-xl px-2 py-2.5 text-[13px] font-medium", view === tab ? "bg-ink text-ink-fg" : "text-muted hover:text-fg")}>{t(viewLabels[tab])}{tab === "evidence" && evidence.length > 0 && <span className="ml-1 text-[10px] opacity-60">{evidence.length}</span>}</button>)}</nav>
    <div role="tabpanel" id={`research-panel-${view}`} aria-labelledby={`research-tab-${view}`} className="space-y-4">
      {view === "overview" && <><ReportPanel reports={reports} current={detail.current_report} busy={busy} onExport={exportReport} /><Panel title={t("Decision conditions")}>{detail.spec.constraints.length === 0 ? <Empty>{t("No conditions recorded.")}</Empty> : <ul className="space-y-2">{detail.spec.constraints.map((c) => <li key={c.id} className="flex gap-2 text-[13px]"><span className="shrink-0 text-muted">{t(c.hard ? "Required" : "Preferred")}</span><span>{c.text}</span></li>)}</ul>}</Panel><SourcesPanel detail={detail} busy={busy} perform={perform} /></>}
      {view === "evidence" && <EvidencePanel evidence={evidence} busy={busy} perform={perform} />}
      {view === "runs" && <RunsPanel runs={detail.runs} busy={busy} perform={perform} />}
      {view === "changes" && <><TrackingPanel key={detail.id} project={detail} busy={busy} perform={perform} /><ChangesPanel changes={changes} busy={busy} perform={perform} /></>}
    </div>
    <details className="rounded-2xl border border-border text-[12px]" open={diagnosticsOpen} onToggle={(e) => {
      const open = e.currentTarget.open; setDiagnosticsOpen(open);
      if (open && !diagnostics) void perform("diagnostics", async () => { setDiagnostics(await researchApi.diagnostics(id)); });
    }}><summary className="cursor-pointer p-4 font-medium">{t("Diagnostics")}</summary><div className="px-4 pb-4"><p className="mb-2 text-muted">{t("Run records, step outputs, model calls and events for this project.")}</p><button type="button" className={small} disabled={busy} onClick={() => void perform("diagnostics", async () => { setDiagnostics(await researchApi.diagnostics(id)); })}>{t("Refresh diagnostics")}</button>{diagnostics ? <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-surface-2 p-3 text-[11px]">{JSON.stringify(diagnostics, null, 2)}</pre> : <Empty>{t("Open or refresh to load diagnostics.")}</Empty>}</div></details>
  </div>;
}

function ReportPanel({ reports, current, busy, onExport }: { reports: ResearchReport[]; current: ResearchReport | null; busy: boolean; onExport: (report: ResearchReport) => Promise<boolean> }) {
  const t = useT();
  const [selected, setSelected] = useState("");
  const report = reports.find((r) => r.id === selected) ?? current ?? reports[0];
  return <Panel title={t("Decision report")} trailing={report && <button type="button" className={small} disabled={busy} onClick={() => void onExport(report)}><Download size={14} />{t("Export Markdown")}</button>}>
    {!report ? <Empty>{t("Start research to collect evidence and publish the first report.")}</Empty> : <><div className="mb-4 flex flex-wrap items-center gap-2">{reports.length > 1 && <select aria-label={t("Report version")} className={cx(inputCls, "w-auto max-w-full text-[12px]")} value={report.id} onChange={(e) => setSelected(e.target.value)}>{reports.map((r) => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString(intlLocale())} · {r.id.slice(0, 8)}</option>)}</select>}<Stamp value={report.created_at} />{report.stale && <Status value="stale" />}</div>{report.stale && <p className="mb-4 rounded-xl bg-amber-500/10 p-3 text-[13px]">{t("Conditions or evidence changed. Run research to update this report.")}</p>}<div className="overflow-x-auto"><Markdown text={report.markdown} /></div><ReportDiff key={report.id} report={report} reports={reports} /></>}
  </Panel>;
}

function ReportDiff({ report, reports }: { report: ResearchReport; reports: ResearchReport[] }) {
  const t = useT();
  const { toast } = useStore();
  const others = reports.filter((r) => r.id !== report.id);
  const [previous, setPrevious] = useState(others[0]?.id ?? "");
  const [diff, setDiff] = useState<ResearchDiff | null>(null);
  const [busy, setBusy] = useState(false);
  if (others.length === 0) return null;
  return <div className="mt-5 border-t border-border pt-4"><h4 className="mb-2 text-[13px] font-semibold">{t("Compare report versions")}</h4><div className="flex flex-wrap gap-2"><select className={cx(inputCls, "min-w-0 flex-1 text-[12px]")} aria-label={t("Previous report")} value={previous} onChange={(e) => { setPrevious(e.target.value); setDiff(null); }}>{others.map((r) => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString(intlLocale())} · {r.id.slice(0, 8)}</option>)}</select><button type="button" className={small} disabled={busy || !previous} onClick={() => { setBusy(true); void researchApi.diff(report.id, previous).then(setDiff).catch((e: Error) => toast(e.message)).finally(() => setBusy(false)); }}>{busy && <Loader2 size={12} className="animate-spin" />}{t("Show report changes")}</button></div>{diff && <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-2xl bg-surface-2 p-3 text-[12px]">{diff.diff || t("No report text changed.")}</pre>}</div>;
}

function SourcesPanel({ detail, busy, perform }: { detail: ResearchProjectDetail; busy: boolean; perform: Perform }) {
  const t = useT();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<"document" | "manual">("document");
  const [candidate, setCandidate] = useState("");
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [content, setContent] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (await perform("source", () => researchApi.addSource(detail.id, { kind, candidate, title: title.trim(), url: kind === "document" ? url.trim() : "", content: kind === "manual" ? content.trim() : "" }), "Source added. Run research to include it.")) { setAdding(false); setTitle(""); setUrl(""); setContent(""); }
  };
  return <Panel title={t("Research sources")} trailing={<button type="button" className={small} disabled={busy} onClick={() => setAdding(!adding)}><Plus size={14} />{t("Add source")}</button>}>
    {detail.sources.length === 0 ? <Empty>{t("No sources registered yet.")}</Empty> : <ul className="divide-y divide-border">{detail.sources.map((s) => <li key={s.id} className="space-y-1 py-3"><div className="flex flex-wrap gap-2 text-[13px]"><span className="font-medium">{s.title || s.url || t("Manual source")}</span><span className="text-muted">{t(s.kind === "manual" ? "Manual text" : "Public source")}</span></div>{s.url && <SourceLink url={s.url} />}{s.candidate && <p className="break-all text-[12px] text-muted">{s.candidate}</p>}{s.status && <Status value={s.status} />}</li>)}</ul>}
    {adding && <form onSubmit={(e) => void submit(e)} className="mt-3 space-y-3 border-t border-border pt-4"><fieldset className="space-y-3" disabled={busy}><Field label={t("Source type")}><select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={inputCls}><option value="document">{t("Official document URL")}</option><option value="manual">{t("Manual text")}</option></select></Field><Field label={t("Source title")}><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} required /></Field><Field label={t("Related repository")}><select className={inputCls} value={candidate} onChange={(e) => setCandidate(e.target.value)}><option value="">{t("Project context")}</option>{detail.spec.candidates.map((c) => <option key={c} value={c}>{c}</option>)}</select></Field>{kind === "document" ? <Field label={t("Official document URL")} hint={t("Public GitHub and approved official documentation sources are supported.")}><input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} type="url" maxLength={2000} required /></Field> : <Field label={t("Source text")} hint={t("Manual text is recorded as supplied context and is not independently verified.")}><textarea className={inputCls} rows={5} value={content} onChange={(e) => setContent(e.target.value)} maxLength={200000} required /></Field>}</fieldset><div className="flex gap-2"><button type="submit" className={primary} disabled={busy}>{t("Save source")}</button><button type="button" className={small} disabled={busy} onClick={() => setAdding(false)}>{t("Cancel")}</button></div></form>}
  </Panel>;
}

function EvidencePanel({ evidence, busy, perform }: { evidence: ResearchEvidence[]; busy: boolean; perform: Perform }) {
  const t = useT();
  const [candidate, setCandidate] = useState("");
  const [status, setStatus] = useState("");
  const [dimension, setDimension] = useState("");
  const [correcting, setCorrecting] = useState<ResearchEvidence | null>(null);
  const filtered = evidence.filter((e) => (!candidate || e.candidate === candidate) && (!status || e.status === status) && (!dimension || e.dimension === dimension));
  return <Panel title={t("Evidence ledger")}><p className="mb-4 text-[13px] leading-relaxed text-muted">{t("Each fact keeps its quote, source version and verification level. Corrections retain their history.")}</p><div className="mb-4 grid gap-2 sm:grid-cols-3">{[["Candidate filter", candidate, setCandidate, [...new Set(evidence.map((e) => e.candidate))]], ["Dimension filter", dimension, setDimension, [...new Set(evidence.map((e) => e.dimension))]], ["Status filter", status, setStatus, [...new Set(evidence.map((e) => e.status))]]].map(([label, value, set, options]) => <select key={label as string} aria-label={t(label as string)} className={cx(inputCls, "text-[12px]")} value={value as string} onChange={(e) => (set as (v: string) => void)(e.target.value)}><option value="">{t(label as string)}</option>{(options as string[]).map((v) => <option key={v} value={v}>{v}</option>)}</select>)}</div>
    {filtered.length === 0 ? <Empty>{t("No evidence matches. Start research or adjust the filters.")}</Empty> : <div className="space-y-3">{filtered.map((e) => <article key={e.id} className="rounded-2xl border border-border p-4"><div className="mb-2 flex flex-wrap items-center gap-2"><span className="text-[12px] font-semibold">{e.dimension}</span><Status value={e.status} /><span className="text-[11px] text-muted">{t(({ documentation: "Documented", source: "Checked in source", inference: "Inference", unknown: "Unverified" } as Record<string, string>)[e.verification] ?? e.verification)}</span></div><p className="whitespace-pre-wrap text-[14px] leading-relaxed">{e.fact}</p>{e.quote && <blockquote className="my-3 border-l-2 border-border pl-3 text-[12px] leading-relaxed text-muted">{e.quote}</blockquote>}<div className="space-y-1">{e.source_url && <SourceLink url={e.source_url} />}<p className="break-all text-[11px] text-muted">{e.candidate} {e.version && `· ${e.version}`} {e.locator && `· ${e.locator}`}</p><Stamp value={e.fetched_at} /></div><div className="mt-3 flex gap-2"><button type="button" className={small} disabled={busy} onClick={() => setCorrecting(e)}>{t("Correct evidence")}</button><button type="button" className={small} disabled={busy} onClick={() => void perform(`verify-${e.id}`, () => researchApi.reverify(e.id), "Verification scheduled.")}>{t("Reverify")}</button></div>{correcting?.id === e.id && <CorrectionForm key={e.id} evidence={correcting} busy={busy} perform={perform} onClose={() => setCorrecting(null)} />}</article>)}</div>}
  </Panel>;
}

function CorrectionForm({ evidence, busy, perform, onClose }: { evidence: ResearchEvidence; busy: boolean; perform: Perform; onClose: () => void }) {
  const t = useT();
  const [fact, setFact] = useState(evidence.fact);
  const [quote, setQuote] = useState(evidence.quote);
  const [reason, setReason] = useState("");
  return <form className="mt-4 space-y-3 border-t border-border pt-4" onSubmit={(e) => { e.preventDefault(); void perform("correct", () => researchApi.correct(evidence.id, fact.trim(), quote.trim(), reason.trim()), "Correction saved. The report needs to be updated.").then((ok) => { if (ok) onClose(); }); }}><fieldset disabled={busy} className="space-y-3"><Field label={t("Corrected fact")}><textarea className={inputCls} rows={3} value={fact} onChange={(e) => setFact(e.target.value)} required /></Field><Field label={t("Supporting quote")}><textarea className={inputCls} rows={2} maxLength={2000} value={quote} onChange={(e) => setQuote(e.target.value)} /></Field><Field label={t("Reason for correction")}><textarea className={inputCls} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} required /></Field></fieldset><div className="flex gap-2"><button type="submit" className={primary} disabled={busy}>{t("Save correction")}</button><button type="button" className={small} disabled={busy} onClick={onClose}>{t("Cancel")}</button></div></form>;
}

function RunsPanel({ runs, busy, perform }: { runs: ResearchRun[]; busy: boolean; perform: Perform }) {
  const t = useT();
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<ResearchRun | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const run = runs.find((r) => r.id === selected) ?? runs[0];
  const runId = run?.id;
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    void researchApi.run(runId).then((r) => { if (!cancelled) { setDetail(r); setError(""); } }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [runId, runs, reload]);
  const current = detail?.id === runId ? detail : run;
  const controlLabels: Record<string, string> = { pause: "Pause run", resume: "Resume run", cancel: "Cancel run", retry: "Retry run" };
  const stageLabels: Record<string, string> = { collect: "Collect sources", extract: "Extract evidence", compare: "Compare candidates", publish: "Publish report" };
  return <Panel title={t("Run history")}>
    {runs.length === 0 ? <Empty>{t("No runs yet. Start research from the project header.")}</Empty> : <><select aria-label={t("Select run")} value={runId} onChange={(e) => { setSelected(e.target.value); setDetail(null); }} className={inputCls}>{runs.map((r) => <option key={r.id} value={r.id}>{new Date(r.created_at).toLocaleString(intlLocale())} · {r.kind} · {r.status}</option>)}</select>{error && <LoadError message={error} onRetry={() => setReload((n) => n + 1)} />}{current && <><div className="my-4 flex flex-wrap items-center gap-2"><Status value={current.status} /><Stamp value={current.created_at} /></div><p className="break-all text-[11px] text-muted">{t("Run ID")}: {current.id}<br />{t("Specification version")}: {current.spec_id}</p>{current.error && <p role="alert" className="mt-3 rounded-xl bg-amber-500/10 p-3 text-[13px]">{current.error}</p>}<div className="mt-3 flex flex-wrap gap-2">{runControls(current.status).map((action) => <button type="button" className={small} disabled={busy} key={action} onClick={() => void perform(action, () => researchApi.control(current.id, action), "Run control saved.")}>{t(controlLabels[action])}</button>)}</div><ol className="mt-4 space-y-2">{current.steps?.map((step) => <li key={step.id} className="rounded-2xl border border-border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-[13px] font-medium">{t(stageLabels[step.stage] ?? step.stage)}</span><Status value={step.status} /></div><p className="mt-1 text-[11px] text-muted">{t("Attempt {n}", { n: step.attempt })}</p>{step.error && <p className="mt-2 text-[12px] text-amber-700 dark:text-amber-300">{step.error}</p>}{step.output != null && <details className="mt-2 text-[12px]"><summary className="cursor-pointer text-muted">{t("Step output")}</summary><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-surface-2 p-3 text-[11px]">{typeof step.output === "string" ? step.output : JSON.stringify(step.output, null, 2)}</pre></details>}</li>)}</ol></>}</>}
  </Panel>;
}

function TrackingPanel({ project, busy, perform }: { project: ResearchProjectDetail; busy: boolean; perform: Perform }) {
  const t = useT();
  const [enabled, setEnabled] = useState(project.tracking?.enabled ?? false);
  const [interval, setInterval] = useState(project.tracking?.interval_minutes ?? 1440);
  const [timezone, setTimezone] = useState(project.tracking?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [time, setTime] = useState(project.tracking?.daily_time ?? "");
  const [maxCalls, setMaxCalls] = useState(project.tracking?.max_calls ?? 40);
  const [dirty, setDirty] = useState(false);
  const tracking = project.tracking;
  useEffect(() => {
    if (dirty) return;
    setEnabled(tracking?.enabled ?? false);
    setInterval(tracking?.interval_minutes ?? 1440);
    setTimezone(tracking?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
    setTime(tracking?.daily_time ?? ""); setMaxCalls(tracking?.max_calls ?? 40);
  }, [tracking, dirty]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value: ResearchTracking = { enabled, interval_minutes: interval, timezone, daily_time: time, max_calls: maxCalls };
    void perform("tracking", () => researchApi.tracking(project.id, value), "Tracking settings saved.").then((ok) => { if (ok) setDirty(false); });
  };
  return <Panel title={t("Source tracking")}><form className="space-y-3" onSubmit={submit}><fieldset disabled={busy} className="space-y-3" onChange={() => setDirty(true)}><label className="flex items-center gap-2 text-[14px]"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />{t("Track source changes")}</label><p className="text-[12px] leading-relaxed text-muted">{t("Changes that affect a condition or recommendation can create a notification. Unrelated changes stay in the event log.")}</p><div className="grid gap-3 sm:grid-cols-2"><Field label={t("Check interval in minutes")}><input type="number" className={inputCls} value={interval} min={1} max={10080} required onChange={(e) => setInterval(Number(e.target.value))} /></Field><Field label={t("Model call limit per check")}><input type="number" className={inputCls} value={maxCalls} min={1} max={100} required onChange={(e) => setMaxCalls(Number(e.target.value))} /></Field><Field label={t("Time zone")}><input className={inputCls} value={timezone} required onChange={(e) => setTimezone(e.target.value)} /></Field><Field label={t("Daily check time")} hint={t("Leave empty to check by interval only.")}><input type="time" className={inputCls} value={time} onChange={(e) => setTime(e.target.value)} /></Field></div></fieldset><button type="submit" className={primary} disabled={busy || !dirty}><Check size={14} />{t("Save tracking")}</button></form></Panel>;
}

function ChangesPanel({ changes, busy, perform }: { changes: ResearchChanges; busy: boolean; perform: Perform }) {
  const t = useT();
  const actionLabels: Record<string, string> = { accept: "Accept change", useless: "Not relevant", mute: "Mute this source", unmute: "Unmute this source" };
  return <><Panel title={t("Notifications")}>{changes.notifications.length === 0 ? <Empty>{t("No actionable notifications.")}</Empty> : <div className="space-y-3">{changes.notifications.map((n) => <article key={n.id} className="rounded-2xl border border-border p-3"><Stamp value={n.created_at} /><div className="my-2 overflow-x-auto"><Markdown text={n.content} /></div><div className="flex flex-wrap items-center gap-2">{(["useful", "useless", "read"] as const).map((feedback) => <button type="button" key={feedback} className={cx(small, n.feedback === feedback && "bg-surface-2")} disabled={busy || n.feedback === feedback} onClick={() => void perform("feedback", () => researchApi.feedback(n.id, feedback), "Feedback saved.")}>{t(({ useful: "Useful", useless: "Not useful", read: "Mark read" })[feedback])}</button>)}</div></article>)}</div>}</Panel><Panel title={t("Change events")}>{changes.events.length === 0 ? <Empty>{t("No source changes recorded.")}</Empty> : <div className="space-y-3">{changes.events.map((event) => {
    const judgment = event.judgment;
    const summary = event.change_summary || event.summary || (typeof judgment?.change_summary === "string" ? judgment.change_summary : "");
    const impact = event.impact_level || (typeof judgment?.impact_level === "string" ? judgment.impact_level : "");
    const action = event.recommended_action || (typeof judgment?.recommended_action === "string" ? judgment.recommended_action : "");
    const muted = changes.mute_rules.some((rule) => rule.source_id && rule.source_id === event.source_id && rule.enabled !== false);
    return <article key={event.id} className="rounded-2xl border border-border p-3"><div className="flex flex-wrap items-center gap-2"><Stamp value={event.created_at} />{impact && <Status value={impact} />}{event.status && <Status value={event.status} />}</div><p className="mt-2 text-[14px] leading-relaxed">{summary || t("Source content changed.")}</p>{action && <p className="mt-1 text-[13px] text-muted">{action}</p>}<div className="mt-3 flex flex-wrap gap-2">{(["accept", "useless", muted ? "unmute" : "mute"] as const).map((a) => <button type="button" className={small} key={a} disabled={busy || event.action === a} onClick={() => void perform("change", () => researchApi.changeAction(event.id, a), "Change action saved.")}>{t(actionLabels[a])}</button>)}</div><details className="mt-3 text-[12px]"><summary className="cursor-pointer text-muted">{t("Change evidence")}</summary><pre className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-surface-2 p-3 text-[11px]">{JSON.stringify(event, null, 2)}</pre></details></article>;
  })}</div>}</Panel><Panel title={t("Muted sources")}>{changes.mute_rules.filter((r) => r.enabled !== false).length === 0 ? <Empty>{t("No active mute rules.")}</Empty> : <ul className="space-y-2">{changes.mute_rules.filter((r) => r.enabled !== false).map((rule) => <li key={rule.id} className="break-all text-[12px] text-muted">{rule.source_id || rule.id}</li>)}</ul>}</Panel></>;
}
