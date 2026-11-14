import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { motion, MotionConfig } from "motion/react";
import { ArrowLeft, ArrowRight, ArrowUp, Check, ChevronRight, House, Mail, MessageCircle, Monitor, MoreHorizontal, Plus, RefreshCw } from "lucide-react";
import { Toaster } from "sonner";
import { TooltipProvider } from "../components/ui/tooltip";
import { WaitPanel } from "../components/SetupWait";
import * as api from "../lib/api";
import { describeBot } from "../lib/bot";
import { Chat } from "./Chat";
import { Computer } from "./Computer";
import { Requests, useRequests } from "./Requests";
import { Review } from "./Review";
import { useWorkspace } from "./useWorkspace";
import { destinationOptions } from "./actions";
import "../index.css";
import "../prototype/style.css";
import "./compact.css";
import "./live.css";

const titleFor = (row?: api.SetupRecord, status?: api.BotStatus) => row?.setup.draft.name ?? (status ? "Default bot" : "Bot");
const readRoute = () => location.hash.slice(1) || "home";
function App() {
  const data = useWorkspace();
  const [route, setRoute] = useState(readRoute);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const scrolls = useRef(new Map<string, number>());
  const [destination, setDestination] = useState("new");
  useEffect(() => { if (data.loaded && !data.enabled) setDestination(current => current === "new" ? "chat" : current); }, [data.loaded, data.enabled]);
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => { const changed = () => { setRoute(readRoute()); setError(""); }; window.addEventListener("hashchange", changed); return () => window.removeEventListener("hashchange", changed); }, []);
  const navigate = (value: string) => { location.hash = value; setRoute(value); setError(""); };
  const draft = (key: string) => drafts[key] ?? "";
  const put = (key: string, value: string) => setDrafts(previous => ({ ...previous, [key]: value }));
  const fresh = data.loaded && !data.error;
  const run = async (action: () => Promise<void>) => {
    if (guard.current || !fresh) return;
    guard.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : "The action was not confirmed. Refresh before trying again."); }
    finally { guard.current = false; setBusy(false); }
  };
  const propose = () => run(async () => {
    const job = draft("home").trim(); if (!job) return;
    if (destination === "new") {
      const row = await api.createSetup(job); await data.refresh(); put("home", ""); navigate(`setup/${encodeURIComponent(row.setup.id)}`);
    } else if (destination === "chat") {
      put("chat/new", job); put("home", ""); navigate("chat/new");
    } else {
      const target = destinationOptions(data.rows).find(item => item.id === destination) ?? data.bots.filter(item => item.bot_id === destination).map(item => ({ chatId: item.chat_id }))[0];
      if (!target) throw new Error("This bot is not ready. Refresh before sending.");
      await api.sendMessage(target.chatId, job, { mode: "queue" }); put("home", ""); await data.refresh(); navigate(`bot/${encodeURIComponent(destination)}`);
    }
  });
  const split = route.indexOf("/");
  const page = split < 0 ? route : route.slice(0, split);
  let id = "";
  try { id = split < 0 ? "" : decodeURIComponent(route.slice(split + 1)); } catch { id = ""; }
  const row = data.rows.find(item => item.setup.id === id);
  const status = data.bots.find(item => item.bot_id === id);
  const names = new Map(data.rows.map(item => [item.setup.id, item.setup.draft.name]));
  const waiting = data.rows.filter(item => item.setup.wait || item.phase === "Investigate" || item.phase === "Exhausted");
  const attention = data.bots.filter(item => item.waiting || item.budget_hold || item.unresolved || item.ceiling_request);
  const results = data.bots.flatMap(bot => bot.tasks.filter(task => task.state === "done").map(task => ({ bot, task }))).slice(0, 8);
  const openBot = (botId: string) => navigate(`bot/${encodeURIComponent(botId)}`);
  const openSetup = (item: api.SetupRecord) => navigate(`${item.runtime_ready ? "bot" : "setup"}/${encodeURIComponent(item.setup.id)}`);
  const bots = [...data.rows.map(item => ({ id: item.setup.id, name: item.setup.draft.name, row: item, status: data.bots.find(bot => bot.bot_id === item.setup.id) })), ...data.bots.filter(bot => !names.has(bot.bot_id)).map(bot => ({ id: bot.bot_id, name: "Default bot", row: undefined, status: bot }))];
  return <MotionConfig reducedMotion="user" transition={{ duration: .18, ease: [0.22, 1, 0.36, 1] }}><TooltipProvider><div className="shell compact experience">
    <aside className="rail"><button className="brand" onClick={() => navigate("home")}>deck</button><button aria-label="Home" onClick={() => navigate("home")}><House size={18}/></button><button aria-label="Conversations" onClick={() => navigate("history")}><MessageCircle size={18}/></button></aside>
    <div className="app">
      {page !== "bot" && <header className="experience-header">{page === "home" ? <h1>deck</h1> : <><button className="icon" aria-label="Back to home" onClick={() => navigate("home")}><ArrowLeft size={18}/></button><h1>{page === "setup" ? "Review setup" : page === "new" ? "New bot" : page === "history" ? "Conversations" : "Chat with Deck"}</h1></>}<button className="icon" aria-label="Refresh workspace" disabled={busy} onClick={() => { void data.refresh(); }}><RefreshCw size={18}/></button>{page === "home" && <button className="icon" aria-label="Conversations" onClick={() => navigate("history")}><MessageCircle size={18}/></button>}</header>}
      {(data.error || error) && <div role="alert" className="error-note">{data.error ?? error}<button className="text-button" onClick={() => { setError(""); void data.refresh(); }}>Refresh</button></div>}
      {!data.loaded ? <main className="content"><p role="status">Loading your workspace…</p></main> : <>
        {(page === "home" || page === "new") && <>
          <main className="content" key={page} ref={element => { if (element) element.scrollTop = scrolls.current.get(page) ?? 0; }} onScroll={event => scrolls.current.set(page, event.currentTarget.scrollTop)}>
            {page === "new" ? <section className="new-job"><h2>What should this bot do?</h2><p>Describe the job below. You’ll review the setup before a computer is created.</p><p className="fine-print">Do not put credentials in this description.</p></section> : <>
              <div className="section-heading"><h2>Your workspace</h2><button className="text-button" disabled={!data.enabled || !fresh} onClick={() => { setDestination("new"); navigate("new"); }}><Plus size={18}/> New bot</button></div>
              {(waiting.length > 0 || attention.length > 0) && <section aria-label="Needs you" className="attention-list"><h2>Needs you</h2>{waiting.map(item => <button className="attention" key={item.setup.id} onClick={() => openSetup(item)}><span><strong>{item.setup.draft.name}</strong><small>{item.setup.wait ? "Sign-in needs your help" : item.phase === "Investigate" ? "An outcome needs investigation" : "Setup attempts exhausted"}</small></span><ChevronRight size={18}/></button>)}{attention.filter(bot => !waiting.some(item => item.setup.id === bot.bot_id)).map(bot => <button className="attention" key={bot.bot_id} onClick={() => navigate(`bot/${encodeURIComponent(bot.bot_id)}?requests`)}><span><strong>{names.get(bot.bot_id) ?? "Default bot"}</strong><small>{describeBot(bot).label}</small></span><ChevronRight size={18}/></button>)}</section>}
              <section aria-label="Your bots" className="bot-list">{bots.map(item => <button key={item.id} className="bot-list-row" onClick={() => item.row ? openSetup(item.row) : openBot(item.id)}><span className="bot-icon"><Mail size={18}/></span><span className="bot-row-copy"><strong>{item.name}</strong><span>{item.status?.working?.title ?? item.row?.setup.draft.instructions ?? "Ready for a task"}</span><small><span className="status-dot"/>{item.status ? describeBot(item.status).label : item.row?.phase === "Draft" ? "Review setup" : item.row?.phase ?? "Not ready"}{item.status?.queued ? ` · ${item.status.queued} queued` : ""}</small></span><ChevronRight size={18}/></button>)}{!bots.length && <div className="empty-result"><p>No bots yet. Describe a job below to create one.</p>{!data.enabled && <p>Bot creation is not configured on this server. You can still chat with Deck.</p>}</div>}</section>
              <section className="results"><div className="section-heading"><h2>Recent results</h2></div>{results.map(({ bot, task }) => <button className="result-row" key={task.id} onClick={() => openBot(bot.bot_id)}><Check size={18}/><span><strong>{task.title}</strong><small>{names.get(bot.bot_id) ?? "Default bot"} · Open conversation</small></span><ChevronRight size={18}/></button>)}{!results.length && <p className="empty-result">Completed work will appear here</p>}</section>
            </>}
          </main>
          <form className="compact-composer" onSubmit={event => { event.preventDefault(); void propose(); }}><div className="composer-context"><span>To Deck</span><select aria-label="Task destination" value={destination} onChange={event => setDestination(event.target.value)}><option value="new" disabled={!data.enabled}>Create a bot</option><option value="chat">Chat with Deck</option>{bots.filter(item => item.status).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div><div className="composer-input"><textarea maxLength={20000} rows={1} aria-label="Describe a job" placeholder={destination === "new" ? "Describe your bot’s job" : "Describe a task"} value={draft("home")} onChange={event => put("home", event.target.value)}/><button className="send" aria-label="Continue" disabled={busy || !fresh || !draft("home").trim() || (destination === "new" && !data.enabled)}><ArrowUp size={18}/></button></div></form>
        </>}
        {page === "history" && <main className="content"><button className="primary" onClick={() => navigate("chat/new")}><Plus size={18}/> New conversation</button><div className="conversation-list">{data.chats.map(chat => <button key={chat.id} className="bot-list-row" onClick={() => chat.bot_id ? openBot(chat.bot_id) : navigate(`chat/${chat.id}`)}><MessageCircle size={18}/><span>{chat.title || "Untitled conversation"}</span><ChevronRight size={18}/></button>)}</div></main>}
        {page === "chat" && (id === "new" || /^\d+$/.test(id)) && <Chat key={route} id={id === "new" ? null : Number(id)} bot={false} name="Deck" draft={draft(route)} setDraft={text => put(route, text)} disabled={!fresh} onCreated={chatId => { navigate(`chat/${chatId}`); }} refresh={data.refresh} scrolls={scrolls.current} scope={route}/>}
        {page === "setup" && (row ? <Setup key={`${row.setup.id}-${row.version}`} row={row} data={data} openBot={openBot} disabled={!fresh}/> : <main className="content"><p>Setup is not available. Refresh the workspace.</p></main>)}
        {page === "bot" && <BotRoute key={id.split("?")[0]} id={id.split("?")[0]} initialTab={id.endsWith("?requests") ? "requests" : "chat"} data={data} home={() => navigate("home")} draft={draft(`bot/${id.split("?")[0]}`)} putDraft={text => put(`bot/${id.split("?")[0]}`, text)} scrolls={scrolls.current} disabled={!fresh}/>}
        {!["home", "new", "history", "setup", "chat", "bot"].includes(page) && <main className="content"><p>This screen is unavailable.</p><button className="secondary" onClick={() => navigate("home")}>Go home</button></main>}
      </>}
    </div><Toaster position="top-center" richColors/>
  </div></TooltipProvider></MotionConfig>;
}

function Setup({ row, data, openBot, disabled }: { row: api.SetupRecord; data: ReturnType<typeof useWorkspace>; openBot: (id: string) => void; disabled: boolean }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [attempts, setAttempts] = useState(String(row.setup.max_attempts + 1));
  const guard = useRef(false);
  const control = async (command: unknown) => {
    if (guard.current || disabled) return; guard.current = true; setBusy(true);
    try { await api.controlSetup(row.setup.id, row.version, command); await data.refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Setup change was not confirmed."); }
    finally { guard.current = false; setBusy(false); }
  };
  return <main className="content setup-view"><h2>{row.setup.draft.name}</h2>{row.phase === "Draft" ? <Review row={row} disabled={disabled} refresh={data.refresh}/> : <>
    <p>{row.runtime_ready ? "Your bot is ready" : row.phase === "Ready" ? "Preparing the runtime" : row.phase}</p><ul className="setup-checklist">{row.checklist.map(item => <li key={item.label}><span>{item.done ? <Check size={18}/> : "·"}</span>{item.label}</li>)}</ul>
    {(row.error || error) && <p className="error-note" role="alert">{row.error ?? error}</p>}
    <fieldset disabled={disabled || busy}>{row.setup.wait && <WaitPanel row={row} protectedInput={data.protectedInput} refresh={data.refresh}/>}</fieldset>
    {row.phase === "Investigate" && <p role="status">The last step has an unknown outcome. Investigate it before any retry. No automatic retry is offered here.</p>}
    {row.phase === "Exhausted" && <form onSubmit={event => { event.preventDefault(); const value = Number(attempts); if (Number.isInteger(value) && value > row.setup.max_attempts) void control({ action: "attempts", value }); }}><label>Total attempt limit<input type="number" min={row.setup.max_attempts + 1} step={1} value={attempts} onChange={event => setAttempts(event.target.value)}/></label><button className="secondary" disabled={disabled || busy}>Set limit</button></form>}
    <div className="review-actions">{row.runtime_ready ? <button className="primary" onClick={() => openBot(row.setup.id)}>Open bot <ArrowRight size={18}/></button> : <><button className="secondary" disabled={disabled || busy} onClick={() => { void control({ action: "pause", value: true }); }}>Pause setup</button><button className="secondary" disabled={disabled || busy || row.phase === "Investigate"} onClick={() => { void control({ action: "pause", value: false }); }}>Resume setup</button><button className="secondary" onClick={() => { void data.refresh(); }}>Refresh progress</button></>}</div>
  </>}</main>;
}

function BotRoute({ id, initialTab, data, home, draft, putDraft, scrolls, disabled }: { id: string; initialTab: string; data: ReturnType<typeof useWorkspace>; home: () => void; draft: string; putDraft: (text: string) => void; scrolls: Map<string, number>; disabled: boolean }) {
  const row = data.rows.find(item => item.setup.id === id), status = data.bots.find(item => item.bot_id === id);
  return status ? <BotWorkspace id={id} initialTab={initialTab} row={row} status={status} data={data} home={home} draft={draft} putDraft={putDraft} scrolls={scrolls} disabled={disabled}/> : <main className="content"><button className="text-button" onClick={home}><ArrowLeft size={18}/> Home</button><p>This bot’s runtime is not ready. Open its setup from Home.</p></main>;
}
function BotWorkspace({ id, initialTab, row, status, data, home, draft, putDraft, scrolls, disabled }: { id: string; initialTab: string; row?: api.SetupRecord; status: api.BotStatus; data: ReturnType<typeof useWorkspace>; home: () => void; draft: string; putDraft: (text: string) => void; scrolls: Map<string, number>; disabled: boolean }) {
  const [tab, setTab] = useState(initialTab);
  useEffect(() => setTab(initialTab), [initialTab]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const guard = useRef(false);
  const requests = useRequests(id);
  const name = titleFor(row, status);
  const tabs = ["chat", "activity", "requests", "computer"];
  const count = requests.items.length + requests.secrets.length + (row?.setup.wait ? 1 : 0);
  const run = async (action: () => Promise<unknown>) => {
    if (guard.current || disabled) return;
    guard.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); await data.refresh(); await requests.refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "The action was not confirmed."); }
    finally { guard.current = false; setBusy(false); }
  };
  const privateScreen = !!row?.setup.wait || !!requests.secrets.length || !!requests.error || !requests.loaded;
  const queued = status.tasks.filter(task => ["queued", "held", "waiting"].includes(task.state));
  return <div className="bot-workspace">
    <header className="experience-header"><button className="icon" aria-label="Back to home" onClick={home}><ArrowLeft size={18}/></button><div className="header-title"><h1>{name}</h1><p role="status">{describeBot(status).label}</p></div><details className="controls-menu"><summary aria-label="Bot controls"><MoreHorizontal size={20}/></summary><div><button disabled={disabled || busy} onClick={() => { void run(() => api.botPause(!status.automation_paused, id)); }}>{status.automation_paused ? "Resume automation" : "Pause automation"}</button><p>Pause prevents new tasks. Current work can continue.</p><button disabled={disabled || busy} onClick={() => { void run(async () => { const result = await api.botStop(id); setNotice(result.unconfirmed.length ? `Stop requested. ${result.unconfirmed.length} command outcomes remain unconfirmed. Check Requests.` : "Stop confirmed"); }); }}>Stop work</button><button disabled={disabled || busy || status.computer === "waking" || status.computer === "saving"} onClick={() => { void run(() => status.computer === "awake" ? api.botSleep(id) : api.botWake(id)); }}>{status.computer === "awake" ? "Sleep computer" : "Wake computer"}</button></div></details></header>
    <div className="workspace-tabs" role="tablist" aria-label="Bot workspace">{tabs.map((value, index) => <button key={value} id={`workspace-tab-${value}`} role="tab" aria-selected={tab === value} aria-controls={`workspace-panel-${value}`} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)} onKeyDown={event => { const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1; if (next >= 0) { event.preventDefault(); setTab(tabs[next]); document.getElementById(`workspace-tab-${tabs[next]}`)?.focus(); } }}>{value}{value === "requests" && count > 0 && <span>{count}</span>}{tab === value && <motion.span layoutId="active-workspace-tab" className="tab-underline"/>}</button>)}</div>
    {(notice || error) && <p className={error ? "error-note" : "inline-status"} role={error ? "alert" : "status"}>{error || notice}</p>}
    {count > 0 && tab === "chat" && <button className="request-banner" onClick={() => setTab("requests")}>{count} request{count === 1 ? "" : "s"} need your attention <ChevronRight size={18}/></button>}
    <div className="chat-tab" role="tabpanel" id="workspace-panel-chat" aria-labelledby="workspace-tab-chat" hidden={tab !== "chat"}><Chat id={status.chat_id} bot name={name} draft={draft} setDraft={putDraft} disabled={disabled} hidden={tab !== "chat"} onCreated={() => {}} refresh={data.refresh} scrolls={scrolls} scope={`bot/${id}`}/></div>
    {tab !== "chat" && <main key={tab} className="content" role="tabpanel" id={`workspace-panel-${tab}`} aria-labelledby={`workspace-tab-${tab}`} tabIndex={0} ref={element => { if (element) element.scrollTop = scrolls.get(`${id}/${tab}`) ?? 0; }} onScroll={event => scrolls.set(`${id}/${tab}`, event.currentTarget.scrollTop)}>
      {tab === "activity" && <><h2>Tasks</h2>{!status.tasks.length && <p className="empty-result">No tasks yet</p>}<ul className="task-list">{status.tasks.map(task => <li key={task.id}><div><strong>{task.title}</strong><span className="task-state">{task.state}</span></div>{task.detail && <p>{task.detail}</p>}{["queued", "held", "failed", "waiting"].includes(task.state) && <div className="task-controls">{task.state === "held" && <button className="text-button" disabled={disabled || busy} onClick={() => { void run(() => api.botRetryTask(task.id, id)); }}>Retry</button>}{task.state !== "failed" && <button className="text-button" disabled={disabled || busy} onClick={() => { void run(() => api.botCancelTask(task.id, id)); }}>Cancel</button>}{task.state === "queued" && <select aria-label={`Priority for ${task.title}`} value={task.priority} disabled={disabled || busy} onChange={event => { void run(() => api.botPriority(task.id, Number(event.target.value), id)); }}><option value={0}>Low priority</option><option value={1}>Normal priority</option><option value={2}>High priority</option></select>}</div>}</li>)}</ul>{queued.length > 0 && <p className="fine-print">Queued work starts only when the server’s pause, permission and spending checks allow it.</p>}{row && <details className="advanced"><summary>Approved setup</summary><p>{row.setup.draft.instructions}</p><p>{row.setup.draft.authority === "Full" ? "Full authority within this job" : row.setup.draft.authority.Restricted.join(", ") || "No actions granted"}</p>{row.setup.draft.budget && <p>Reviewed ceiling: {(row.setup.draft.budget.ceiling / 10 ** row.setup.draft.budget.exponent).toFixed(row.setup.draft.budget.exponent)} {row.setup.draft.budget.currency}</p>}</details>}</>}
      {tab === "requests" && <Requests id={id} row={row} protectedInput={data.protectedInput} requests={requests} disabled={disabled} refresh={data.refresh}/>}
      {tab === "computer" && <><div className="computer-toolbar"><span>{status.computer}</span><button className="secondary" disabled={disabled || busy || status.computer === "saving" || status.computer === "waking"} onClick={() => { void run(() => status.computer === "awake" ? api.botSleep(id) : api.botWake(id)); }}>{status.computer === "awake" ? "Sleep" : "Wake"}</button></div>{disabled ? <p className="empty-result">Refresh the connection before viewing the computer.</p> : privateScreen ? <div className="live-screen"><p>Screen hidden while sign-in or protected input needs attention</p><button className="secondary" onClick={() => setTab("requests")}>Open requests</button></div> : status.computer === "awake" ? <Computer key={id} id={id}/> : <div className="live-screen"><Monitor size={28}/><p>{status.computer === "saved" ? "The computer is asleep. Wake it to see the screen." : "Waiting for the computer to be ready"}</p></div>}</>}
    </main>}
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><App/></StrictMode>);
