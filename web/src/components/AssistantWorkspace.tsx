import { Bell, BookOpen, ChevronDown, ChevronRight, FlaskConical, FolderOpen, ListTodo, Maximize2, MessageSquare, Minus, Monitor, Moon, PanelLeftClose, PanelLeftOpen, Plus, Search, Settings2, ShieldCheck, Sun, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, request } from "../api";
import { desktopBridge } from "../desktop";
import { useLocale, useT } from "../i18n";
import { useStore, type Tab } from "../store";
import { setThemeSetting, useTheme } from "../theme";
import { cx } from "../util";
import { useFocusTrap } from "./useFocusTrap";

export function AssistantMark({ size = 28 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path d="M7 23V9l9 14V9M20 9v14m5-14v14" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

const NAV: Array<{ id: Tab; label: string; icon: typeof ListTodo }> = [
  { id: "chat", label: "Conversations", icon: MessageSquare },
  { id: "tasks", label: "Task ledger", icon: ListTodo },
  { id: "goals", label: "Plans & reminders", icon: Bell },
  { id: "library", label: "Workspace", icon: FolderOpen },
  { id: "research", label: "Research module", icon: FlaskConical },
  { id: "memory", label: "Personal memory", icon: BookOpen },
];
const PAGE_LABELS: Partial<Record<Tab, string>> = Object.fromEntries(NAV.map(({ id, label }) => [id, label]));

/** Window chrome contains no model, file or execution privileges. */
export function AssistantWindowBar() {
  const t = useT();
  const bridge = desktopBridge();
  return <header className="assistant-windowbar">
    <span className="assistant-window-label"><AssistantMark size={19} /> nanoMuse <span>{t("Personal assistant")}</span></span>
    {bridge?.windowAction && <div className="assistant-window-controls">
      <button onClick={() => bridge.windowAction?.("minimize")} aria-label={t("Minimize window")}><Minus size={15} /></button>
      <button onClick={() => bridge.windowAction?.("maximize")} aria-label={t("Maximize or restore window")}><Maximize2 size={12} /></button>
      <button onClick={() => bridge.windowAction?.("close")} aria-label={t("Close window")} className="window-close"><X size={17} /></button>
    </div>}
  </header>;
}

type RuntimeInventory = { mode: "offline" | "live"; usage_limits?: { configuration_required: boolean } };
export function useAssistantMode() {
  const { state } = useStore();
  const [inventory, setInventory] = useState<RuntimeInventory | null>(null);
  useEffect(() => {
    let alive = true;
    request<RuntimeInventory>("/api/assistant/capabilities").then((value) => { if (alive) setInventory(value); }).catch(() => { if (alive) setInventory(null); });
    return () => { alive = false; };
  }, [state.connectionsVersion]);
  return inventory;
}

export function AssistantWorkspace({ children }: { children: ReactNode }) {
  const { state, setTab, openThread, dispatch, toast } = useStore();
  const t = useT();
  const theme = useTheme();
  const inventory = useAssistantMode();
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);
  useFocusTrap(searchRef, searchOpen);
  const searchButton = useRef<HTMLButtonElement>(null);
  const create = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const thread = await api.createThread(t("New conversation"));
      dispatch({ type: "ws", msg: { kind: "thread", thread } });
      openThread(thread.id);
      requestAnimationFrame(() => window.dispatchEvent(new Event("nanomuse:quick-chat")));
    } catch (error) { toast((error as Error).message); }
    finally { setCreating(false); }
  };
  const newChat = useRef(create);
  useEffect(() => { newChat.current = create; });
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setSearchOpen((value) => !value);
      }
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "o") {
        event.preventDefault(); void newChat.current();
      }
      if (event.key === "Escape" && searchOpen) { setSearchOpen(false); searchButton.current?.focus(); }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [searchOpen]);
  const go = (tab: Tab) => { setTab(tab); setSearchOpen(false); };
  const threads = state.threads.filter((thread) => !thread.device && !thread.remote_from);
  const matching = threads.filter((thread) => (thread.id === "main" ? t("Main conversation") : thread.title).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const activeThread = state.threads.find((thread) => thread.id === state.activeThread);
  const pageLabel = state.tab === "chat" ? activeThread?.id === "main" ? t("Your assistant") : activeThread?.title || t("Conversations") : t(PAGE_LABELS[state.tab] || ({ you: "Settings", connections: "Connections", feed: "Activity", skills: "Skills", devices: "Devices", account: "Account" } as Partial<Record<Tab, string>>)[state.tab] || "nanoMuse");
  const status = !state.connected ? t("Reconnecting…") : state.pendingApprovals.length ? t("Waiting for your review") : state.status.state === "working" ? t("Working…") : t("Ready");
  return <div className="assistant-desktop">
    <AssistantWindowBar />
    <div className={cx("assistant-body", collapsed && "sidebar-collapsed")}>
      <aside className="assistant-sidebar" aria-label={t("Main navigation")}>
        <div className="assistant-brand"><span className="assistant-brandmark"><AssistantMark size={31} /></span><span className="assistant-wordmark">nanoMuse<span>{t("A little help, every day.")}</span></span><button className="assistant-icon" onClick={() => setCollapsed(true)} aria-label={t("Collapse sidebar")} title={t("Collapse sidebar")}><PanelLeftClose size={17} /></button></div>
        <button className="assistant-new-chat" onClick={() => void create()} disabled={creating}><Plus size={17} /> {t("New conversation")}<kbd>Ctrl ⇧ O</kbd></button>
        <button ref={searchButton} className="assistant-search-trigger" onClick={() => setSearchOpen(true)}><Search size={16} /><span>{t("Search conversations")}</span><kbd>Ctrl K</kbd></button>
        <nav className="assistant-nav">{NAV.map(({ id, label, icon: Icon }) => <button key={id} className={cx("assistant-nav-item", state.tab === id && "is-active")} onClick={() => go(id)} aria-current={state.tab === id ? "page" : undefined}><Icon size={18} strokeWidth={1.65} /><span>{t(label)}</span>{id === "tasks" && state.pendingApprovals.length > 0 && <span className="assistant-count">{state.pendingApprovals.length}</span>}</button>)}</nav>
        <div className="assistant-recents"><span className="assistant-section-label">{t("Recent conversations")}</span><div>{threads.slice(0, 12).map((thread) => <button key={thread.id} onClick={() => openThread(thread.id)} className={cx("assistant-recent", state.tab === "chat" && state.activeThread === thread.id && "is-active")} title={thread.id === "main" ? t("Main conversation") : thread.title}><span className="assistant-recent-dot" /><span>{thread.id === "main" ? t("Main conversation") : thread.title}</span>{thread.busy && <span className="assistant-working-dot" />}</button>)}</div></div>
        <div className="assistant-sidebar-bottom">
          <button className="assistant-review-link" onClick={() => go("feed")}><ShieldCheck size={17} /><span>{state.pendingApprovals.length ? t("{n} approvals to review", { n: state.pendingApprovals.length }) : t("Actions stay under your control")}</span><ChevronRight size={14} /></button>
          <div className="assistant-profile-row"><button className="assistant-profile" onClick={() => go("you")}><span className="assistant-profile-monogram">N</span><span><strong>{t("Personal workspace")}</strong><small>{inventory?.mode === "offline" ? t("Offline scripted responses") : t("Local on this computer")}</small></span><Settings2 size={16} /></button><button className="assistant-icon" aria-label={t(theme === "dark" ? "Use light appearance" : "Use dark appearance")} onClick={() => setThemeSetting(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</button></div>
        </div>
      </aside>
      <section className="assistant-workarea">
        <div className="assistant-pagebar"><div>{collapsed && <button className="assistant-icon" onClick={() => setCollapsed(false)} aria-label={t("Expand sidebar")}><PanelLeftOpen size={19} /></button>}<span className="assistant-breadcrumb">{t("Personal workspace")}</span><ChevronRight size={12} /><span className="assistant-page-name">{pageLabel}</span></div><button className="assistant-runtime-status" onClick={() => go("connections")} title={t("Connections")}><span className={cx("assistant-status-dot", !state.connected && "is-disconnected")} />{status}<span className="assistant-runtime-mode">{inventory?.mode === "offline" ? t("Offline") : t("Local")}</span></button></div>
        <main className={cx("assistant-content", state.tab !== "chat" && "assistant-module-content")} key="content">{children}</main>
        {state.toast && <div role="status" className="assistant-toast">{state.toast}</div>}
      </section>
    </div>
    {searchOpen && <div className="assistant-dialog-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSearchOpen(false); }}><div ref={searchRef} role="dialog" aria-modal="true" aria-label={t("Search conversations")} className="assistant-command-dialog"><label><Search size={19} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Search conversations")} aria-label={t("Search conversations")} /><button className="assistant-icon" onClick={() => setSearchOpen(false)} aria-label={t("Close")}><X size={17} /></button></label><div className="assistant-search-results">{matching.length ? matching.map((thread) => <button key={thread.id} onClick={() => { openThread(thread.id); setSearchOpen(false); }}><MessageSquare size={16} /><span>{thread.id === "main" ? t("Main conversation") : thread.title}</span><ChevronRight size={14} /></button>) : <p>{t("No conversations found.")}</p>}</div><footer>{t("Searches conversation titles on this device.")}</footer></div></div>}
  </div>;
}

/** Picks fill the composer. They never spend tokens or start an action. */
export function AssistantWelcome() {
  const t = useT();
  const locale = useLocale();
  const { draft } = useStore();
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning." : hour < 18 ? "Good afternoon." : "Good evening.";
  const suggestions = [
    { icon: ListTodo, title: "Plan my day", detail: "Turn a busy day into a clear plan", prompt: "Help me plan my day. First ask about my tasks, deadlines and available time." },
    { icon: FolderOpen, title: "Organize my files", detail: "A tidy workspace, with your review", prompt: "Help me organize workspace files. Inspect the file list first, then show me a plan before making changes." },
    { icon: FlaskConical, title: "Research a question", detail: "Compare options using sources", prompt: "Help me research a decision. Ask about my options and requirements, then collect evidence and compare them." },
  ];
  return <div className="assistant-welcome"><div className="assistant-welcome-eyebrow"><span /><span>{t("SPACE TO THINK. HELP TO ACT.")}</span></div><h1>{t(greeting)}<br /><span>{t("What shall we work on?")}</span></h1><p>{t("Make a plan, find an answer, or take care of the little things.")}</p><div className="assistant-suggestions">{suggestions.map(({ icon: Icon, title, detail, prompt }) => <button key={title} onClick={() => draft(t(prompt))}><Icon size={21} strokeWidth={1.5} /><strong>{t(title)}</strong><span>{t(detail)}</span><ChevronRight size={16} className="suggestion-arrow" /></button>)}</div><div className="assistant-welcome-note"><ShieldCheck size={14} />{t("You review sensitive actions before they run.")}</div><span className="assistant-local-date">{new Intl.DateTimeFormat(locale, { month: "long", day: "numeric", weekday: "long" }).format(new Date())}</span></div>;
}

export function AssistantComposerMeta() {
  const { state, setTab } = useStore();
  const t = useT();
  const inventory = useAssistantMode();
  return <div className="assistant-composer-meta"><button type="button" onClick={() => setTab("connections")}><span className="assistant-model-dot" />{inventory?.mode === "offline" ? t("Offline scripted responses") : state.settings?.llm.model || t("Configure a model")}<ChevronDown size={12} /></button><span><Monitor size={12} />{t("On this computer")}</span></div>;
}
