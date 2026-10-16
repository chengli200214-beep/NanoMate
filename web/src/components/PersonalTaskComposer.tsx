import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "../api";
import { useT } from "../i18n";

type Reminder = { id: string; text: string; revision: number; repeat: string; next_at: string; status: string };
type Preference = { key: string; value: string; revision: number; purpose: string; status: string };
type Kind = "reminder" | "document" | "preference";
type Timing = "once" | "repeat" | "keep";
const inputClass = "w-full rounded-xl border border-line bg-surface p-3 text-sm";
const buttonClass = "rounded-xl border border-line px-3 py-2 text-sm disabled:opacity-40";
const future = (value: string) => Boolean(value && Number.isFinite(new Date(value).getTime()) && new Date(value).getTime() > Date.now());

export function PersonalTaskComposer({ onCreated }: { onCreated: () => Promise<void> | void }) {
  const t = useT();
  const [kind, setKind] = useState<Kind>("reminder");
  const [title, setTitle] = useState("");
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [preferences, setPreferences] = useState<Preference[]>([]);
  const [reminderAction, setReminderAction] = useState("create");
  const [reminderId, setReminderId] = useState("");
  const [reminderText, setReminderText] = useState("");
  const [timing, setTiming] = useState<Timing>("once");
  const [at, setAt] = useState("");
  const [repeat, setRepeat] = useState("");
  const [sourcePaths, setSourcePaths] = useState("");
  const [outputName, setOutputName] = useState("digest.md");
  const [notes, setNotes] = useState("");
  const [preferenceAction, setPreferenceAction] = useState("set");
  const [preferenceKey, setPreferenceKey] = useState("");
  const [preferenceValue, setPreferenceValue] = useState("");
  const [purpose, setPurpose] = useState("");
  const [expiry, setExpiry] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [created, setCreated] = useState(false);
  const mounted = useRef(true);
  const retry = useRef({ payload: "", key: "" });
  const reminder = reminders.find((row) => row.id === reminderId);
  const preference = preferences.find((row) => row.key === preferenceKey);

  const load = useCallback(async () => {
    const [rows, values] = await Promise.all([
      request<Reminder[]>("/api/reminders"), request<Preference[]>("/api/assistant/preferences"),
    ]);
    if (mounted.current) { setReminders(rows); setPreferences(values); setLoadFailed(false); }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void load().catch(() => { if (mounted.current) setLoadFailed(true); });
    return () => { mounted.current = false; };
  }, [load]);

  const paths = sourcePaths.split("\n").map((path) => path.trim()).filter(Boolean);
  const reminderValid = reminderAction === "cancel" ? Boolean(reminder) :
    Boolean(reminderText.trim() && (reminderAction === "create" || reminder) &&
      ((timing === "keep" && reminderAction === "update") || (timing === "once" && future(at)) || (timing === "repeat" && repeat.trim())));
  const documentValid = paths.length > 0 && paths.length <= 10 &&
    paths.every((path) => /\.(md|txt)$/i.test(path)) && /^[^\\/:*?"<>|]+\.md$/i.test(outputName.trim());
  const preferenceValid = /^[a-z][a-z0-9_.-]{0,63}$/.test(preferenceKey) && confirmed &&
    (preferenceAction === "delete" ? preference?.status === "active" : Boolean(preferenceValue.trim() && (!expiry || future(expiry))));
  const valid = title.trim() && ({ reminder: reminderValid, document: documentValid, preference: preferenceValid })[kind];

  const createTask = async () => {
    if (!valid || busy) return;
    setBusy(true); setFailed(false); setCreated(false);
    const payload: Record<string, unknown> = kind === "reminder" ? {
      action: reminderAction,
      ...(reminderAction !== "create" ? { reminder_id: reminderId, expected_revision: reminder?.revision } : {}),
      ...(reminderAction !== "cancel" ? { text: reminderText.trim(), ...(timing === "once" ? { at: new Date(at).toISOString() } : timing === "repeat" ? { repeat: repeat.trim() } : {}) } : {}),
    } : kind === "document" ? {
      source_paths: paths, output_name: outputName.trim(), notes,
    } : {
      action: preferenceAction, key: preferenceKey, expected_revision: preference?.revision ?? 0,
      ...(preferenceAction === "set" ? { value: preferenceValue.trim(), purpose: purpose.trim(), ...(expiry ? { expires_at: new Date(expiry).getTime() / 1000 } : {}) } : {}),
    };
    const criteria = t(kind === "reminder" ? "The reminder change has a verified module receipt. Delivery is tracked separately." : kind === "document" ?
      "The Markdown artifact exists and its hash matches its publication receipt." : "The confirmed preference change has been projected into personal memory.");
    const body = { plan: { title: title.trim(), success_criteria: criteria, steps: [{ adapter: kind, title: title.trim(), [kind]: payload }] },
      ...(kind === "preference" && confirmed ? { confirm_preferences: true } : {}) };
    const serialized = JSON.stringify(body);
    if (retry.current.payload !== serialized) retry.current = { payload: serialized, key: crypto.randomUUID() };
    try {
      await request("/api/assistant/tasks", { method: "POST", body: JSON.stringify({ ...body, request_id: retry.current.key }) });
      retry.current = { payload: "", key: "" };
      if (mounted.current) { setCreated(true); setTitle(""); setConfirmed(false); }
      try { await onCreated(); } catch { if (mounted.current) setLoadFailed(true); }
      try { await load(); } catch { if (mounted.current) setLoadFailed(true); }
    } catch { if (mounted.current) setFailed(true); }
    finally { if (mounted.current) setBusy(false); }
  };

  return <section className="space-y-3 rounded-2xl border border-line p-4" aria-label={t("Personal task")}>
    <h2 className="font-medium">{t("Personal task")}</h2>
    <p className="text-sm text-muted">{t("Changes are recorded as versioned task steps. Review the task result after execution.")}</p>
    {failed && <p role="alert">{t("Could not confirm task creation. Retry the unchanged form to reuse its request ID.")}</p>}
    {created && <p role="status">{t("Task created. Its execution result will appear in the ledger.")}</p>}
    {loadFailed && <p role="alert">{t("Could not refresh reminders or preferences. Refresh before editing existing records.")}</p>}
    <button type="button" className={buttonClass} disabled={busy} onClick={() => void load().catch(() => setLoadFailed(true))}>{t("Refresh personal records")}</button>
    <label className="block">{t("Personal task type")}<select className={inputClass} value={kind} disabled={busy} onChange={(event) => { setKind(event.target.value as Kind); setConfirmed(false); setCreated(false); }}>
      <option value="reminder">{t("Reminder change")}</option><option value="document">{t("Document artifact")}</option><option value="preference">{t("Personal preference")}</option>
    </select></label>
    <label className="block">{t("Personal task title")}<input className={inputClass} value={title} maxLength={500} disabled={busy} onChange={(event) => setTitle(event.target.value)} /></label>
    {kind === "reminder" && <div className="space-y-3">
      <label className="block">{t("Reminder action")}<select className={inputClass} value={reminderAction} disabled={busy} onChange={(event) => { setReminderAction(event.target.value); setTiming(event.target.value === "update" ? "keep" : "once"); }}>
        <option value="create">{t("Create reminder")}</option><option value="update">{t("Update reminder")}</option><option value="cancel">{t("Cancel reminder")}</option>
      </select></label>
      {reminderAction !== "create" && <label className="block">{t("Existing reminder")}<select className={inputClass} value={reminderId} disabled={busy || loadFailed} onChange={(event) => { setReminderId(event.target.value); setReminderText(reminders.find((row) => row.id === event.target.value)?.text ?? ""); }}>
        <option value="">{t("Choose a reminder")}</option>{reminders.map((row) => <option key={row.id} value={row.id}>{row.text} · {t("Revision")} {row.revision}</option>)}
      </select></label>}
      {reminderAction !== "cancel" && <>
        <label className="block">{t("Reminder text")}<textarea className={inputClass} value={reminderText} maxLength={4000} disabled={busy} onChange={(event) => setReminderText(event.target.value)} /></label>
        <label className="block">{t("Reminder timing")}<select className={inputClass} value={timing} disabled={busy} onChange={(event) => setTiming(event.target.value as Timing)}>
          {reminderAction === "update" && <option value="keep">{t("Keep current time")}</option>}<option value="once">{t("Once at a local time")}</option><option value="repeat">{t("Repeat cadence")}</option>
        </select></label>
        {timing === "once" && <label className="block">{t("Future reminder time (local)")}<input className={inputClass} type="datetime-local" value={at} disabled={busy} onChange={(event) => setAt(event.target.value)} /></label>}
        {timing === "repeat" && <label className="block">{t("Repeat cadence")}<input className={inputClass} value={repeat} maxLength={100} placeholder="daily 08:00" disabled={busy} onChange={(event) => setRepeat(event.target.value)} /><span className="text-sm text-muted">{t("Examples: daily 08:00, weekdays 07:30, weekly mon 09:00, monthly 1 09:00.")}</span></label>}
      </>}
      <p className="text-sm text-muted">{t("Reminder delivery keeps its existing scheduler. A saved reminder does not prove the requested work is complete.")}</p>
    </div>}
    {kind === "document" && <div className="space-y-3">
      <p className="text-sm text-muted">{t("Use UTF-8 .md or .txt files already in the workspace. This produces bounded source excerpts with hashes, not a semantic summary.")}</p>
      <label className="block">{t("Workspace source paths, one per line")}<textarea className={inputClass} value={sourcePaths} disabled={busy} onChange={(event) => setSourcePaths(event.target.value)} placeholder="notes/study.md" /></label>
      <label className="block">{t("Markdown output filename")}<input className={inputClass} value={outputName} maxLength={100} disabled={busy} onChange={(event) => setOutputName(event.target.value)} /></label>
      <label className="block">{t("Notes for this artifact (optional)")}<textarea className={inputClass} value={notes} maxLength={12000} disabled={busy} onChange={(event) => setNotes(event.target.value)} /></label>
    </div>}
    {kind === "preference" && <div className="space-y-3">
      <p className="text-sm text-muted">{t("Store only preferences you choose to confirm. Source documents cannot grant this confirmation.")}</p>
      <label className="block">{t("Preference action")}<select className={inputClass} value={preferenceAction} disabled={busy} onChange={(event) => { setPreferenceAction(event.target.value); setConfirmed(false); }}>
        <option value="set">{t("Save or replace preference")}</option><option value="delete">{t("Delete preference")}</option>
      </select></label>
      <label className="block">{t("Existing preference (optional)")}<select className={inputClass} value={preference?.key ?? ""} disabled={busy || loadFailed} onChange={(event) => { const row = preferences.find((item) => item.key === event.target.value); setPreferenceKey(row?.key ?? ""); setPreferenceValue(row?.value ?? ""); setPurpose(row?.purpose ?? ""); setExpiry(""); setConfirmed(false); }}>
        <option value="">{t("New preference")}</option>{preferences.map((row) => <option key={row.key} value={row.key}>{row.key} · {t("Revision")} {row.revision}</option>)}
      </select></label>
      <label className="block">{t("Preference key")}<input className={inputClass} value={preferenceKey} maxLength={64} disabled={busy} placeholder="reply.language" onChange={(event) => { setPreferenceKey(event.target.value.trim()); setConfirmed(false); }} /></label>
      {preferenceAction === "set" && <>
        <label className="block">{t("Preference value")}<textarea className={inputClass} value={preferenceValue} maxLength={2000} disabled={busy} onChange={(event) => { setPreferenceValue(event.target.value); setConfirmed(false); }} /></label>
        <label className="block">{t("Purpose (optional)")}<input className={inputClass} value={purpose} maxLength={1000} disabled={busy} onChange={(event) => { setPurpose(event.target.value); setConfirmed(false); }} /></label>
        <label className="block">{t("Preference expiry (local, optional)")}<input className={inputClass} type="datetime-local" value={expiry} disabled={busy} onChange={(event) => { setExpiry(event.target.value); setConfirmed(false); }} /></label>
      </>}
      <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />{t("Confirm this personal preference")}</label>
    </div>}
    <button type="button" className={buttonClass} disabled={busy || !valid || (loadFailed && kind === "reminder" && reminderAction !== "create") || (loadFailed && kind === "preference")} onClick={() => void createTask()}>{t("Create personal task")}</button>
  </section>;
}
