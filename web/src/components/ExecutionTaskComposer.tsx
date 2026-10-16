import { useRef, useState } from "react";
import { fileUrl, request } from "../api";
import { useT } from "../i18n";

type Selection = { id: string; revision: number; status: string; steps?: { result: Record<string, unknown> }[] };
const field = "w-full rounded-xl border border-line bg-surface p-3 text-sm";
const button = "rounded-xl border border-line px-3 py-2 text-sm disabled:opacity-40";

export function ExecutionTaskComposer({ selected, onCreated }: { selected: Selection | null; onCreated: () => Promise<void> }) {
  const t = useT();
  const [kind, setKind] = useState("file");
  const [action, setAction] = useState("write");
  const [title, setTitle] = useState("");
  const [path, setPath] = useState("");
  const [source, setSource] = useState("");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [index, setIndex] = useState("");
  const [app, setApp] = useState("notepad");
  const [keys, setKeys] = useState("ctrl+s");
  const [hash, setHash] = useState("");
  const [coordinates, setCoordinates] = useState("0,0");
  const [reason, setReason] = useState("");
  const [observed, setObserved] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const retry = useRef({ signature: "", key: "" });
  const receiptObservation = selected?.steps?.map(s => s.result.observation).filter((o): o is Record<string, unknown> => typeof o === "object" && o !== null && "observation_id" in o).slice(-1)[0];
  const labels: Record<string, string> = { write: "Write file", copy: "Copy file", move: "Move file", mkdir: "Create folder", navigate: "Open page", extract: "Extract page text", fill: "Fill draft field", download: "Download observed link", click: kind === "browser" ? "Follow ordinary link" : "Click observed target", observe: "Observe current state", open_app: "Open application", type: "Type text", key: "Press keys", verify_saved_file: "Verify saved file" };
  const actions = kind === "file" ? ["write", "copy", "move", "mkdir"] : kind === "browser" ? ["navigate", "extract", "fill", "click", "download"] : ["observe", "open_app", "type", "key", "click", "verify_saved_file"];
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : t("Execution request failed")); }
    finally { setBusy(false); }
  };
  const observe = () => run(async () => {
    setObserved(await request<Record<string, unknown>>("/api/assistant/execution/observe", { method: "POST", body: JSON.stringify({ surface: kind }) }));
  });
  const submit = (replace: boolean) => run(async () => {
    let payload: Record<string, unknown>;
    if (kind === "file") payload = { action, path, ...(action === "write" ? { content: text } : {}), ...(["copy", "move"].includes(action) ? { source_path: source } : {}) };
    else if (kind === "browser") payload = { action, ...(action === "navigate" ? { url } : {}), ...(["fill", "click", "download"].includes(action) ? { index: Number(index), expected_url: observed?.url, observation_hash: observed?.observation_hash } : {}), ...(action === "fill" ? { text } : {}), ...(action === "download" ? { path } : {}) };
    else {
      const [x, y] = coordinates.split(",").map(Number);
      payload = { action, app, ...(action !== "observe" ? { expected_observation: observed?.observation_id } : {}), ...(action === "open_app" && path ? { path } : {}), ...(action === "type" ? { text } : {}), ...(action === "key" ? { keys: keys.split("+").map(s => s.trim()) } : {}), ...(action === "click" ? { x, y, label: text } : {}), ...(action === "verify_saved_file" ? { saved_file: path, expected_sha256: hash } : {}) };
    }
    const plan = { title, success_criteria: t("The action has a recorded result. Input dispatch alone does not prove the requested work is complete."), steps: [{ adapter: kind, title, [kind]: payload }] };
    const endpoint = replace && selected ? `/api/assistant/tasks/${selected.id}/plan-revisions` : "/api/assistant/tasks";
    const body = { plan, ...(replace && selected ? { expected_revision: selected.revision, reason } : {}) };
    const signature = JSON.stringify([endpoint, body]);
    if (retry.current.signature !== signature) retry.current = { signature, key: crypto.randomUUID() };
    await request(endpoint, { method: "POST", body: JSON.stringify({ ...body, request_id: retry.current.key }) });
    retry.current = { signature: "", key: "" }; setObserved(null); await onCreated();
  });
  return <section className="space-y-3 rounded-2xl border border-line p-4">
    <h2 className="font-medium">{t("File, browser and Windows tasks")}</h2>
    <p className="text-sm text-muted">{t("Create a step, review its exact preview in the task, then approve it. Browser and Windows input require a fresh observation. Pause before manual recovery.")}</p>
    <label className="block">{t("Task title")}<input className={field} value={title} onChange={e => setTitle(e.target.value)} maxLength={500} /></label>
    <label className="block">{t("Execution surface")}<select className={field} value={kind} onChange={e => { const k = e.target.value; setKind(k); setAction(k === "file" ? "write" : k === "browser" ? "navigate" : "observe"); setObserved(null); }}><option value="file">{t("Workspace files")}</option><option value="browser">{t("Browser")}</option><option value="native">{t("Windows native apps")}</option></select></label>
    {kind === "native" && receiptObservation && <button className={button} disabled={busy} onClick={() => { if (Number(receiptObservation.expires_at) * 1000 <= Date.now()) { setError(t("This observation expired. Observe again before preparing input.")); return; } setObserved(receiptObservation); }}>{t("Use the selected task's latest observation")}</button>}
    <label className="block">{t("Action")}<select className={field} value={action} onChange={e => setAction(e.target.value)}>{actions.map(a => <option key={a} value={a}>{t(labels[a])}</option>)}</select></label>
    {kind !== "file" && <><button className={button} disabled={busy} onClick={() => void observe()}>{t("Observe current state")}</button>{observed && <>{typeof observed.screenshot_path === "string" && <img className="max-h-96 max-w-full object-contain" src={fileUrl(observed.screenshot_path)} alt={t("Current Windows observation")} />}<pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(observed, null, 2)}</pre></>}</>}
    {(kind === "file" || kind === "browser" && action === "download" || kind === "native" && ["open_app", "verify_saved_file"].includes(action)) && <label className="block">{t("Workspace relative target path")}<input className={field} value={path} onChange={e => setPath(e.target.value)} /></label>}
    {kind === "browser" && action === "download" && <p className="text-sm text-muted">{t("Download one public link into a new file, up to 16 MiB. Browser cookies are not sent. The file is not opened or executed.")}</p>}
    {kind === "file" && ["copy", "move"].includes(action) && <label className="block">{t("Workspace relative source path")}<input className={field} value={source} onChange={e => setSource(e.target.value)} /></label>}
    {kind === "browser" && action === "navigate" && <label className="block">{t("Page URL")}<input className={field} value={url} onChange={e => setUrl(e.target.value)} /></label>}
    {kind === "browser" && ["fill", "click", "download"].includes(action) && <label className="block">{t("Observed element index")}<input className={field} type="number" min={0} value={index} onChange={e => setIndex(e.target.value)} /></label>}
    {(kind === "file" && action === "write" || action === "fill" || action === "type" || kind === "native" && action === "click") && <label className="block">{t(action === "click" ? "Visible target label" : "Text")}<textarea className={field} value={text} onChange={e => setText(e.target.value)} /></label>}
    {kind === "native" && <label className="block">{t("Application")}<select className={field} value={app} onChange={e => setApp(e.target.value)}><option value="notepad">{t("Notepad")}</option><option value="explorer">{t("File Explorer")}</option></select></label>}
    {kind === "native" && action === "key" && <label className="block">{t("Key chord")}<input className={field} value={keys} onChange={e => setKeys(e.target.value)} /></label>}
    {kind === "native" && action === "click" && <label className="block">{t("Observed coordinates x,y")}<input className={field} value={coordinates} onChange={e => setCoordinates(e.target.value)} /></label>}
    {kind === "native" && action === "verify_saved_file" && <label className="block">{t("Expected saved file SHA-256")}<input className={field} value={hash} onChange={e => setHash(e.target.value)} /></label>}
    {error && <p role="alert">{error}</p>}
    <button className={button} disabled={busy || !title.trim()} onClick={() => void submit(false)}>{t("Create execution preview")}</button>
    {selected && !["completed", "cancelled", "cancelling"].includes(selected.status) && <><label className="block">{t("Reason for replacing the remaining plan")}<input className={field} value={reason} onChange={e => setReason(e.target.value)} /></label><button className={button} disabled={busy || !title.trim() || !reason.trim()} onClick={() => void submit(true)}>{t("Replace with observed action")}</button></>}
  </section>;
}
