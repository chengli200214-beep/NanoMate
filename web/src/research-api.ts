import { AuthError, failureDetail, getToken, request } from "./api";

export interface ResearchConstraint { id: string; text: string; hard: boolean }
export interface ResearchSpec {
  title: string;
  goal: string;
  candidates: string[];
  constraints: ResearchConstraint[];
  mode: "live" | "fixture";
}
export interface ResearchTracking {
  enabled: boolean;
  interval_minutes: number;
  timezone: string;
  daily_time: string;
  max_calls: number;
}
export interface ResearchSourceInput {
  candidate: string;
  kind: "readme" | "file" | "releases" | "metadata" | "document" | "manual";
  url: string;
  content: string;
  title: string;
}
export interface ResearchSource extends ResearchSourceInput {
  id: string;
  status?: string;
  version?: string;
  fetched_at?: string;
}
export interface ResearchReport {
  id: string; project_id: string; spec_id: string; markdown: string; created_at: string; stale: boolean;
}
export interface ResearchStep {
  id: string; run_id: string; stage: string; status: string; attempt: number; error?: string;
  output?: unknown;
}
export interface ResearchRun {
  id: string; project_id: string; kind: string; spec_id: string; status: string; created_at: string;
  error?: string; steps?: ResearchStep[];
}
export interface ResearchProject {
  id: string; title: string; spec_id: string; created_at: string;
  mode: "live" | "fixture"; tracking?: Partial<ResearchTracking>;
}
export interface ResearchProjectDetail extends ResearchProject {
  spec: ResearchSpec; sources: ResearchSource[]; runs: ResearchRun[]; current_report: ResearchReport | null;
  tracking_blocked?: boolean;
}
export interface ResearchEvidence {
  id: string; project_id: string; candidate: string; dimension: string; fact: string; quote: string;
  snapshot_id: string; locator: string; verification: string; status: string; source_url: string;
  version: string; fetched_at: string;
}
export interface ResearchConflict {
  id: string; project_id: string; candidate: string; dimension: string; evidence_refs: string[];
  reason: string; version_scope: string; status: "open" | "superseded"; created_at: string;
  spec_id: string; run_id: string;
}
export interface ResearchChange {
  id: string; source_id?: string; created_at?: string; status?: string; action?: string;
  summary?: string; change_summary?: string; impact_level?: string; recommended_action?: string;
  judgment?: Record<string, unknown>; [key: string]: unknown;
  spec_id?: string; affected_constraints?: string[]; affected_constraint_refs?: string[]; change_type?: string; feedback?: string;
  notification_policy?: { constraint_refs?: string[] };
}
export interface ResearchNotification {
  id: string; content: string; feedback?: string; created_at: string;
}
export interface ResearchMuteRule {
  id: string; source_id?: string; impact_level?: string; enabled?: boolean;
  scope?: { spec_id?: string; change_type?: string; constraint_refs?: string[] };
  [key: string]: unknown;
}
export function muteMatches(rule: ResearchMuteRule, event: ResearchChange): boolean {
  const refs = [...new Set([...(event.affected_constraints ?? []), ...(event.affected_constraint_refs ?? []), ...(event.notification_policy?.constraint_refs ?? [])])].sort();
  return rule.enabled !== false && !!rule.scope && rule.source_id === event.source_id
    && rule.impact_level === event.impact_level && rule.scope.spec_id === event.spec_id
    && rule.scope.change_type === (event.change_type ?? "content")
    && JSON.stringify(rule.scope.constraint_refs) === JSON.stringify(refs);
}
export interface ResearchChanges {
  events: ResearchChange[]; notifications: ResearchNotification[]; mute_rules: ResearchMuteRule[];
}
export interface ResearchDiagnostics {
  runs: unknown[]; steps: unknown[]; calls: unknown[]; events: unknown[];
}
export interface ResearchDiff { before: string; after: string; diff: string }
export type ResearchControl = "pause" | "resume" | "cancel" | "retry";

const root = "/api/research";
const idPath = (id: string) => encodeURIComponent(id);
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const projectPath = (id: string) => `${root}/projects/${idPath(id)}`;

export const researchApi = {
  projects: () => request<ResearchProject[]>(`${root}/projects`),
  create: (spec: ResearchSpec) => request<ResearchProject>(`${root}/projects`, post(spec)),
  project: (id: string) => request<ResearchProjectDetail>(projectPath(id)),
  revise: (id: string, spec: ResearchSpec) => request<ResearchSpec>(`${projectPath(id)}/revisions`, post(spec)),
  addSource: (id: string, source: ResearchSourceInput) => request<ResearchSource>(`${projectPath(id)}/sources`, post(source)),
  start: (id: string, kind: "research" | "check", requestKey: string) => request<ResearchRun>(`${projectPath(id)}/runs`, post({ kind, request_key: requestKey })),
  run: (id: string) => request<ResearchRun>(`${root}/runs/${idPath(id)}`),
  control: (id: string, action: ResearchControl) => request<ResearchRun>(`${root}/runs/${idPath(id)}/control`, post({ action })),
  evidence: (id: string) => request<ResearchEvidence[]>(`${projectPath(id)}/evidence`),
  conflicts: (id: string) => request<ResearchConflict[]>(`${projectPath(id)}/conflicts`),
  correct: (id: string, fact: string, quote: string, reason: string) => request<ResearchEvidence>(`${root}/evidence/${idPath(id)}/correction`, post({ fact, quote: quote.trim() || null, reason })),
  reverify: (id: string) => request<ResearchRun>(`${root}/evidence/${idPath(id)}/reverify`, post({})),
  reports: (id: string) => request<ResearchReport[]>(`${projectPath(id)}/reports`),
  diff: (id: string, previous: string) => request<ResearchDiff>(`${root}/reports/${idPath(id)}/diff?previous=${idPath(previous)}`),
  tracking: (id: string, tracking: ResearchTracking) => request<ResearchTracking>(`${projectPath(id)}/tracking`, { method: "PUT", body: JSON.stringify(tracking) }),
  changes: (id: string) => request<ResearchChanges>(`${projectPath(id)}/changes`),
  changeAction: (id: string, action: "accept" | "useless" | "mute" | "unmute") => request<ResearchChange>(`${root}/changes/${idPath(id)}/action`, post({ action })),
  feedback: (id: string, feedback: "useful" | "useless" | "read") => request<ResearchNotification>(`${root}/notifications/${idPath(id)}/feedback`, post({ feedback })),
  diagnostics: (id: string) => request<ResearchDiagnostics>(`${projectPath(id)}/diagnostics`),
  exportMarkdown,
};

/** Export stays authenticated, with no token put into the download URL. */
async function exportMarkdown(id: string): Promise<string> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${root}/reports/${idPath(id)}/export`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch {
    throw new Error("Cannot reach your nanoMuse right now.");
  }
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => undefined);
    throw new Error(failureDetail(res.status, body));
  }
  return res.text();
}

/** Reject deep links and credentials so a candidate always identifies a public repository. */
export function repositoryUrl(value: string): string | null {
  try {
    const url = new URL(value.trim());
    const parts = url.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    if (url.protocol !== "https:" || url.hostname !== "github.com" || url.port || url.username || url.password || parts.length !== 2 || url.search || url.hash) return null;
    const [owner, name] = parts;
    if (!/^[\w.-]+$/.test(owner) || !/^[\w.-]+$/.test(name) || [owner, name].some((s) => s === "." || s === "..")) return null;
    const repository = name.replace(/\.git$/, "");
    if (!repository || repository === "." || repository === "..") return null;
    return `https://github.com/${owner}/${repository}`;
  } catch {
    return null;
  }
}

export function isActiveRun(run: ResearchRun): boolean {
  return ["queued", "running", "retry_wait", "recovering"].includes(run.status);
}

export function runControls(status: string): ResearchControl[] {
  if (["queued", "running", "retry_wait", "recovering"].includes(status)) return ["pause", "cancel"];
  if (status === "paused") return ["resume", "cancel"];
  if (status === "failed") return ["retry"];
  if (status === "needs_attention") return ["resume", "retry", "cancel"];
  return [];
}
