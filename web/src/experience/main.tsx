import { StrictMode, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion, MotionConfig, useIsPresent, useReducedMotion } from "motion/react";
import { DropdownMenu } from "radix-ui";
import { ArrowLeft, ArrowRight, ArrowUp, Check, ChevronRight, House, Mail, MessageCircle, Monitor, MoreHorizontal, Plus, RefreshCw } from "lucide-react";
import { Toaster, toast } from "sonner";
import { TooltipProvider } from "../components/ui/tooltip";
import { WaitPanel } from "../components/SetupWait";
import * as api from "../lib/api";
import { describeBot } from "../lib/bot";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { ChoiceSheet } from "./ChoiceSheet";
import { HomeStart, HomeLoading, RouteLoading } from "./HomeStart";
import { createNavigation } from "./navigation";
import { BotChatHost, ChatHost } from "./Chat";
import { moveDraft } from "./chat-flow";
import { Computer } from "./Computer";
import { Requests, RequestsProvider, useRequests } from "./Requests";
import { money, taskKey } from "./requests-store";
import { Recovery, Review } from "./Review";
import { hasUnsaved, isBusy, newEntry } from "./setup-editor";
import { checkAttempts, pauseActions, phaseLabel, resumeBlock, setupHeading, setupKey, stepLines } from "./setup-progress";
import { guarded } from "../lib/wait";
import { useSetupEditors } from "./useSetupEditors";
import { useWorkspace } from "./useWorkspace";
import { destinationOptions } from "./actions";
import "../index.css";
import "../prototype/style.css";
import "./compact.css";
import "./live.css";

const titleFor = (row?: api.SetupRecord, status?: api.BotStatus) => row?.setup.draft.name ?? (status ? "Default bot" : "Bot");
const nav = createNavigation(window);
const readRoute = nav.route;
const headerTitle = (page: string, row?: api.SetupRecord) => page === "setup" ? setupHeading(row) : page === "new" ? "New bot" : page === "history" ? "Conversations" : page === "bot" ? "Bot" : "Chat with Deck";
// One keyed body for each route. The outgoing body is inert while it fades. Focus moves to the heading when the new body mounts.
function RouteUnit({ children, route }: { children: ReactNode; route: string }) {
  const present = useIsPresent(), reduced = useReducedMotion(), unit = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const target = unit.current?.querySelector<HTMLElement>("[data-return-focus=true]") ?? unit.current?.querySelector<HTMLElement>(unit.current.querySelector(".uncertain-create") ? ".bot-list-row, textarea" : "h1, main h2");
      if (target) { if (target.matches("h1, h2")) target.tabIndex = -1; target.focus({ preventScroll: true }); }
    });
    return () => cancelAnimationFrame(frame);
  }, []);
  return <motion.div ref={unit} data-route={route} className="route-unit" inert={!present} initial={reduced ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={reduced ? undefined : { opacity: 0, transition: { duration: .1 } }}>{children}</motion.div>;
}
type BotChatSlot = { slot: HTMLElement | null; hidden: boolean };
type SetBotChatSlot = (id: string, change: Partial<BotChatSlot>) => void;
function App() {
  const data = useWorkspace();
  const [route, setRoute] = useState(readRoute);
  const navigationEpoch = useRef(0);
  const [acceptedSetups, setAcceptedSetups] = useState<api.SetupRecord[]>([]);
  const rows = [...data.rows, ...acceptedSetups.filter(row => !data.rows.some(saved => saved.setup.id === row.setup.id))];
  useEffect(() => { setAcceptedSetups(previous => previous.filter(row => !data.rows.some(saved => saved.setup.id === row.setup.id))); }, [data.rows]);
  const editors = useSetupEditors(rows);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const scrolls = useRef(new Map<string, number>());
  const historySelection = useRef<number | null>(null);
  const [destination, setDestination] = useState("new");
  useEffect(() => { if (data.hasData && !data.enabled) setDestination(current => current === "new" ? "chat" : current); }, [data.hasData, data.enabled]);
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const [error, setError] = useState("");
  const [manualRefresh, setManualRefresh] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [uncertainCreate, setUncertainCreate] = useState(false);
  const refreshWorkspace = () => { setManualRefresh(true); setRefreshMessage("Refreshing…"); void data.refresh(); };
  useEffect(() => { if (manualRefresh && !data.refreshing) { setManualRefresh(false); setRefreshMessage(data.error ? "" : "Workspace is up to date"); } }, [manualRefresh, data.refreshing, data.error]);
  useEffect(() => nav.subscribe(next => { navigationEpoch.current++; setRoute(next); setError(""); }), []);
  const navigate: typeof nav.go = (next, options) => {
    if (next === "history" && nav.route() !== "history") { historySelection.current = null; scrolls.current.set("history", 0); }
    nav.go(next, options);
  }, back = nav.back;
  const draft = (key: string) => drafts[key] ?? "";
  const put = (key: string, value: string) => { if (key === "home") setRefreshMessage(""); setDrafts(previous => ({ ...previous, [key]: value })); };
  const update = useCallback((key: string, change: (current: string) => string) => setDrafts(previous => ({ ...previous, [key]: change(previous[key] ?? "") })), []);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [botSlots, setBotSlots] = useState<Record<string, BotChatSlot>>({});
  const hostBot = useCallback<SetBotChatSlot>((id, change) => setBotSlots(previous => {
    const old = previous[id] ?? { slot: null, hidden: true };
    const next = { ...old, ...change };
    return previous[id] && old.slot === next.slot && old.hidden === next.hidden ? previous : { ...previous, [id]: next };
  }), []);
  const [created, setCreated] = useState<Record<number, string>>({});
  const chatHosted = useRef(false);
  const fresh = data.hasData && !data.error;
  const run = async (action: () => Promise<void>) => {
    if (guard.current || !fresh) return;
    guard.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (e) {
      setError(e instanceof Error ? e.message : "The action was not confirmed. Refresh before trying again.");
      if (["home", "new"].includes(readRoute())) requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>(".app textarea")?.focus());
    }
    finally { guard.current = false; setBusy(false); }
  };
  const propose = (targetDestination = destination) => run(async () => {
    const job = draft("home").trim(); if (!job) return;
    const epoch = navigationEpoch.current, origin = readRoute();
    const clearSentDraft = () => setDrafts(previous => previous.home?.trim() === job ? { ...previous, home: "" } : previous);
    const complete = (path: string, message: string) => {
      clearSentDraft();
      if (navigationEpoch.current === epoch && readRoute() === origin) navigate(path);
      else toast.success(message, { action: { label: "Open", onClick: () => navigate(path) } });
    };
    if (targetDestination === "new") {
      if (uncertainCreate) return;
      let row: api.SetupRecord;
      try { row = await api.createSetup(job); }
      catch (e) {
        if (!(e instanceof api.ApiError) || e.status === 0 || e.status >= 500) { setUncertainCreate(true); void data.refresh(); }
        throw e;
      }
      setAcceptedSetups(previous => [...previous.filter(item => item.setup.id !== row.setup.id), row]);
      complete(`setup/${encodeURIComponent(row.setup.id)}`, "Setup draft created. Ready for review.");
      void data.refresh();
    } else if (targetDestination === "chat") {
      if (draft("chat/new").trim() && draft("chat/new").trim() !== job) {
        toast.info("Your existing chat draft is kept. The new description is saved on Home.");
        navigate("chat/new"); return;
      }
      put("chat/new", job); put("home", ""); navigate("chat/new");
    } else {
      const target = destinationOptions(data.rows).find(item => item.id === targetDestination) ?? data.bots.filter(item => item.bot_id === targetDestination).map(item => ({ chatId: item.chat_id }))[0];
      if (!target) throw new Error("This bot is not ready. Refresh before sending.");
      await api.sendMessage(target.chatId, job, { mode: "queue" });
      complete(`bot/${encodeURIComponent(targetDestination)}`, "Task received by the bot");
      void data.refresh();
    }
  });
  const split = route.indexOf("/");
  const page = split < 0 ? route : route.slice(0, split);
  let id = "";
  try { id = split < 0 ? "" : decodeURIComponent(route.slice(split + 1)); } catch { id = ""; }
  const homeContent = useCallback((element: HTMLElement | null) => { if (element) element.scrollTop = scrolls.current.get(page) ?? 0; }, [page]);
  if (page === "chat") chatHosted.current = true;
  const routeKey = page === "chat" ? "chat" : route.split("?")[0];
  const row = rows.find(item => item.setup.id === id);
  const status = data.bots.find(item => item.bot_id === id);
  const names = new Map(rows.map(item => [item.setup.id, item.setup.draft.name]));
  const waiting = rows.filter(item => item.setup.wait || item.phase === "Investigate" || item.phase === "Exhausted");
  const attention = data.bots.filter(item => item.waiting || item.budget_hold || item.unresolved || item.ceiling_request);
  const results = data.bots.flatMap(bot => bot.tasks.filter(task => task.state === "done").map(task => ({ bot, task }))).slice(0, 8);
  const openBot = (botId: string) => navigate(`bot/${encodeURIComponent(botId)}`);
  const orphaned = (id: string) => { const entry = editors.entries[id]; return !!entry?.left && hasUnsaved(entry); };
  const openSetup = (item: api.SetupRecord) => navigate(`${item.runtime_ready && !orphaned(item.setup.id) ? "bot" : "setup"}/${encodeURIComponent(item.setup.id)}`);
  const bots = [...rows.map(item => ({ id: item.setup.id, name: item.setup.draft.name, row: item, status: data.bots.find(bot => bot.bot_id === item.setup.id) })), ...data.bots.filter(bot => !names.has(bot.bot_id)).map(bot => ({ id: bot.bot_id, name: "Default bot", row: undefined, status: bot }))];
  const homeFeedback = manualRefresh && data.refreshing ? "Refreshing…" : error || data.error || refreshMessage;
  const firstHome = page === "home" && data.hasData && data.enabled && !bots.length;
  const recovery = uncertainCreate ? <section className="uncertain-create" aria-label="Check the previous setup"><p>The last request may have created a setup. Check your workspace before starting another.</p><Button variant="outline" aria-disabled={data.refreshing} onClick={() => { if (!data.refreshing) refreshWorkspace(); }}>Check workspace</Button><Button variant="outline" disabled={!fresh || data.refreshing} onClick={() => { setUncertainCreate(false); setError(""); }}>I checked — allow a new setup</Button></section> : null;
  useLayoutEffect(() => {
    if (!uncertainCreate || !["home", "new", "bot"].includes(page)) return;
    const target = Array.from(document.querySelectorAll<HTMLElement>(".route-unit")).find(unit => unit.dataset.route === routeKey && !unit.hasAttribute("inert"))?.querySelector<HTMLElement>(".bot-list-row, textarea");
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: "nearest", behavior: "instant" });
  }, [firstHome, uncertainCreate, page, routeKey]);
  return <MotionConfig reducedMotion="user" transition={{ duration: .18, ease: [0.22, 1, 0.36, 1] }}><TooltipProvider><div className="shell compact experience">
    <aside className="rail"><button className="brand" onClick={() => navigate("home")}>deck</button><button aria-label="Home" onClick={() => navigate("home")}><House size={18}/></button><button aria-label="Conversations" onClick={() => navigate("history")}><MessageCircle size={18}/></button></aside>
    <div className="app">
      {!data.hasData ? (page === "home" ? <HomeLoading error={data.error} retry={() => { void data.refresh(); }}/> : <RouteLoading title={page === "chat" && id !== "new" ? "Conversation" : headerTitle(page)} back={back} error={data.error} retry={() => { void data.refresh(); }} composer={page === "chat" || page === "new"}/>) : <AnimatePresence mode="wait" initial={false}><RouteUnit key={routeKey} route={routeKey}>
      {page !== "bot" && !firstHome && <header className="experience-header">{page === "home" ? <h1>deck</h1> : <><button className="icon" aria-label="Back" onClick={back}><ArrowLeft size={18}/></button><h1>{page === "chat" && id !== "new" ? data.chats.find(chat => chat.id === Number(id))?.title || created[Number(id)] || "Untitled conversation" : headerTitle(page, row)}</h1></>}<button className="icon" aria-label="Refresh workspace" disabled={busy} onClick={refreshWorkspace}><RefreshCw size={18}/></button>{page === "home" && <button className="icon" aria-label="Conversations" onClick={() => navigate("history")}><MessageCircle size={18}/></button>}</header>}
      {(!["home", "new", "bot"].includes(page) && (data.error || error)) && <div role="alert" className="error-note">{data.error ?? error}<button className="text-button" onClick={() => { setError(""); void data.refresh(); }}>Refresh</button></div>}
      <>
        {firstHome && <HomeStart enter={false} draft={draft("home")} setDraft={text => put("home", text)} submit={() => { void propose("new"); }} disabled={!fresh || uncertainCreate} busy={busy} recovery={recovery} notice={homeFeedback} noticeIsError={!!error} retry={refreshWorkspace} retryBusy={data.refreshing} actions={[{ label: "Chat with Deck", run: () => navigate("chat/new") }, { label: "Conversations", run: () => navigate("history") }, { label: "Refresh workspace", run: refreshWorkspace }]} />}
        {(page === "home" || page === "new") && !firstHome && <>
          <main className="content" key={page} ref={homeContent} onScroll={event => scrolls.current.set(page, event.currentTarget.scrollTop)}>
            {recovery}
            {page === "new" ? <section className="new-job"><h2>What should this bot do?</h2><p>Describe the job below. You’ll review the setup before a computer is created.</p><p className="fine-print">Do not put credentials in this description.</p></section> : <>
              <div className="section-heading"><h2>Your workspace</h2><Button className="text-button" disabled={!data.enabled || !fresh} onClick={() => { setDestination("new"); navigate("new"); }}><Plus size={18}/> New bot</Button></div>
              {(waiting.length > 0 || attention.length > 0) && <section aria-label="Needs you" className="attention-list"><h2>Needs you</h2>{waiting.map(item => <Button className="attention" key={item.setup.id} onClick={() => openSetup(item)}><span><strong>{item.setup.draft.name}</strong><small>{item.setup.wait ? "Sign-in needs your help" : item.phase === "Investigate" ? "An outcome needs investigation" : "Setup attempts exhausted"}</small></span><ChevronRight size={18}/></Button>)}{attention.filter(bot => !waiting.some(item => item.setup.id === bot.bot_id)).map(bot => <Button className="attention" key={bot.bot_id} onClick={() => navigate(`bot/${encodeURIComponent(bot.bot_id)}?requests`)}><span><strong>{names.get(bot.bot_id) ?? "Default bot"}</strong><small>{describeBot(bot).label}</small></span><ChevronRight size={18}/></Button>)}</section>}
              <section aria-label="Your bots" className="bot-list">{bots.map(item => <Button key={item.id} className="bot-list-row" onClick={() => item.row ? openSetup(item.row) : openBot(item.id)}><span className="bot-icon"><Mail size={18}/></span><span className="bot-row-copy"><strong>{item.name}</strong><span>{item.status?.working?.title ?? item.row?.setup.draft.instructions ?? "Ready for a task"}</span><small><span className="status-dot"/>{item.status ? describeBot(item.status).label : item.row ? phaseLabel(item.row) : "Not ready"}{item.status?.queued ? ` · ${item.status.queued} queued` : ""}</small></span><ChevronRight size={18}/></Button>)}{!bots.length && <div className="empty-result"><p>No bots yet. Describe a job below to create one.</p>{!data.enabled && <p>Bot creation is not configured on this server. You can still chat with Deck.</p>}</div>}</section>
              {results.length > 0 && <section className="results"><div className="section-heading"><h2>Completed results</h2></div>{results.map(({ bot, task }) => <Button className="result-row" key={task.id} onClick={() => openBot(bot.bot_id)}><Check size={18}/><span><strong>{task.title}</strong><small>{names.get(bot.bot_id) ?? "Default bot"} · Open conversation</small></span><ChevronRight size={18}/></Button>)}{!results.length && <p className="empty-result">Completed work will appear here</p>}</section>}
            </>}
          </main>
          <form className="compact-composer" onSubmit={event => { event.preventDefault(); void propose(); }}><div className="composer-context"><span>To Deck</span><ChoiceSheet label="Task destination" value={destination} disabled={busy || !fresh} onChange={setDestination} options={[{ value: "new", label: "Create a bot", disabled: !data.enabled }, { value: "chat", label: "Chat with Deck" }, ...bots.filter(item => item.status).map(item => ({ value: item.id, label: item.name }))]}/></div><div className="composer-input"><Textarea maxLength={20000} rows={1} aria-label="Describe a job" placeholder={destination === "new" ? "Describe your bot’s job" : "Describe a task"} value={draft("home")} onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} onChange={event => put("home", event.target.value)}/><Button className="send" aria-label="Continue" disabled={busy || !fresh || !draft("home").trim() || (destination === "new" && (!data.enabled || uncertainCreate))}><ArrowUp size={18}/></Button></div><div className="home-send-status"><span role={error ? "alert" : "status"}>{busy ? destination === "new" ? "Preparing your setup…" : "Sending your task…" : homeFeedback}</span>{(data.error || error) && <Button type="button" variant="ghost" disabled={data.refreshing} onClick={refreshWorkspace}>Refresh</Button>}</div></form>
        </>}

        {page === "history" && <main className="content" ref={homeContent} onScroll={event => scrolls.current.set("history", event.currentTarget.scrollTop)}><button className="primary" onClick={() => { historySelection.current = null; navigate("chat/new"); }}><Plus size={18}/> New conversation</button>{!fresh && <p className="fine-print">Showing the last known list. You can read conversations; sending resumes when Deck reconnects.</p>}{!data.chats.length && <p className="empty-result">No conversations yet. Start one with Deck.</p>}<div className="conversation-list">{data.chats.map(chat => <button key={chat.id} className="bot-list-row" data-return-focus={historySelection.current === chat.id ? "true" : undefined} onClick={() => { historySelection.current = chat.id; chat.bot_id ? openBot(chat.bot_id) : navigate(`chat/${chat.id}`); }}><MessageCircle size={18}/><span className="conversation-copy"><strong>{chat.title || "Untitled conversation"}</strong><small>{chat.bot_id ? names.get(chat.bot_id) || "Bot conversation" : "Deck"}</small></span><ChevronRight size={18}/></button>)}</div>{data.chats.length > 0 && <p className="fine-print">Newest conversations first.</p>}</main>}
        {page === "chat" && (id === "new" || /^\d+$/.test(id)) && <div className="chat-slot" ref={setSlot}/>}
        {page === "setup" && (row ? <Setup key={setupKey(row)} row={row} data={data} openBot={openBot} disabled={!fresh} editors={editors}/> : <main className="content"><p>Setup is not available. Refresh the workspace.</p></main>)}
        {page === "bot" && <BotRoute key={id.split("?")[0]} id={id.split("?")[0]} initialTab={id.endsWith("?requests") ? "requests" : "chat"} data={data} back={back} hostChat={hostBot} scrolls={scrolls.current} disabled={!fresh}/>}
        {!["home", "new", "history", "setup", "chat", "bot"].includes(page) && <main className="content"><p>This screen is unavailable.</p><button className="secondary" onClick={() => navigate("home", { replace: true })}>Go home</button></main>}
      </>
    </RouteUnit></AnimatePresence>}
    {Object.entries(botSlots).map(([botId, target]) => <BotChatHost key={botId} id={data.bots.find(bot => bot.bot_id === botId)?.chat_id ?? null} name={names.get(botId) ?? "Bot"} slot={target.slot} hidden={target.hidden} disabled={!fresh} draft={draft(`bot/${botId}`)} setDraft={text => put(`bot/${botId}`, text)} putDraft={(_key, change) => update(`bot/${botId}`, change)} refresh={data.refresh} scrolls={scrolls.current} scope={`bot/${botId}`}/>)}
    {chatHosted.current && <ChatHost routeId={page === "chat" && /^\d+$/.test(id) ? Number(id) : null} route={page === "chat" && (id === "new" || /^\d+$/.test(id)) ? route : ""} slot={page === "chat" ? slot : null} name="Deck" draft={draft(route)} setDraft={text => put(route, text)} disabled={!fresh} onCreated={(chatId, title) => { setDrafts(previous => moveDraft(previous, chatId)); setCreated(previous => ({ ...previous, [chatId]: title })); if (nav.route() === "chat/new") navigate(`chat/${chatId}`, { replace: true }); }} refresh={data.refresh} scrolls={scrolls.current} putDraft={update}/>}
    </div><Toaster position="top-center" richColors/>
  </div></TooltipProvider></MotionConfig>;
}

function Setup({ row, data, openBot, disabled, editors }: { row: api.SetupRecord; data: ReturnType<typeof useWorkspace>; openBot: (id: string) => void; disabled: boolean; editors: ReturnType<typeof useSetupEditors> }) {
  // The screen keeps one key per setup. Typing, focus, scroll, notes and the guard survive version updates.
  const [pending, setPending] = useState(""); const [note, setNote] = useState<{ text: string; error: boolean } | null>(null);
  const [attempts, setAttempts] = useState(String(row.setup.max_attempts + 1)); const [attemptsError, setAttemptsError] = useState("");
  const [asked, setAsked] = useState(false); const [refreshNote, setRefreshNote] = useState("");
  const guard = useRef({ busy: false }), limit = useRef<HTMLInputElement>(null);
  useEffect(() => { if (asked && !data.refreshing) { setAsked(false); setNote(previous => previous?.error ? previous : null); setRefreshNote(data.error ? "Refresh failed. Showing the last known progress." : "Progress is up to date."); } }, [asked, data.refreshing, data.error]);
  useEffect(() => { if (!data.error) setRefreshNote(previous => previous.startsWith("Refresh failed.") ? "Connection restored. Progress is up to date." : previous); }, [data.error]);
  const control = (command: unknown, working: string, done: string) => disabled ? Promise.resolve(false) : guarded(guard.current, async () => {
    setPending(working); setNote(null); setRefreshNote("");
    try { await api.controlSetup(row.setup.id, row.version, command); setNote({ text: done, error: false }); await data.refresh(); }
    catch (e) { setNote({ text: e instanceof Error ? e.message : "Setup change was not confirmed.", error: true }); }
    finally { setPending(""); }
  });
  const entry = editors.entries[row.setup.id], orphan = row.phase !== "Draft" && !!entry && hasUnsaved(entry) && !isBusy(entry);
  const block = resumeBlock(row), off = disabled || !!pending, steps = row.phase === "Investigate" ? stepLines(row) : [];
  const actions = pauseActions(data.bots.find(b => b.bot_id === row.setup.id)?.automation_paused);
  const status = pending || (note && !note.error ? note.text : "") || (asked ? "Refreshing…" : refreshNote);
  return <main className="content setup-view"><h2 tabIndex={-1}>{row.setup.draft.name}</h2>{row.phase === "Draft" ? <Review row={row} entry={editors.entries[row.setup.id] ?? newEntry(row)} patch={editors.patch} guard={editors.guard} disabled={disabled} refresh={data.refresh}/> : <>
    <p>{phaseLabel(row)}</p>
    {row.error && <p className="error-note" role="alert">{row.error}</p>}
    {note?.error && <p className="error-note" role="alert">{note.text}</p>}
    {row.setup.wait && <fieldset disabled={disabled}><WaitPanel row={row} protectedInput={data.protectedInput} refresh={data.refresh}/></fieldset>}
    {row.phase === "Investigate" && <section aria-label="Unknown outcome"><p>The last step has an unknown outcome. Deck cannot resolve it here yet. Check the outside service for what happened. No automatic retry is offered.</p>{steps.length > 0 && <ul className="setup-lines">{steps.map(line => <li key={line}>{line}</li>)}</ul>}</section>}
    {row.phase === "Exhausted" && <form className="setup-attempts" noValidate onSubmit={event => {
      event.preventDefault();
      const result = checkAttempts(attempts, row.setup.max_attempts);
      if (!result.ok) { setAttemptsError(result.message); limit.current?.focus(); return; }
      setAttemptsError(""); void control({ action: "attempts", value: result.value }, "Setting the limit…", `Attempt limit set to ${result.value}.`);
    }}><p>Setup used all {row.setup.max_attempts} attempts. Raise the total limit to try again.</p><label>Total attempt limit<input ref={limit} id="setup-attempts" type="text" inputMode="numeric" autoComplete="off" value={attempts} aria-invalid={!!attemptsError} aria-describedby={attemptsError ? "setup-attempts-error" : undefined} onChange={event => { setAttempts(event.target.value); setAttemptsError(""); }}/></label>{attemptsError && <p id="setup-attempts-error" className="field-error" role="alert">{attemptsError}</p>}<button className="secondary" aria-disabled={off}>Set limit</button></form>}
    <ul className="setup-checklist">{row.checklist.map(item => <li key={item.label}><span>{item.done ? <Check size={18}/> : "·"}</span>{item.label}</li>)}</ul>
    {status && <p className="inline-status" role="status">{status}</p>}
    <div className="review-actions setup-actions">{row.runtime_ready ? <button className="primary" onClick={() => openBot(row.setup.id)}>Open bot <ArrowRight size={18}/></button> : <>{actions.pause && <button className="secondary" aria-disabled={off} onClick={() => { void control({ action: "pause", value: true }, "Requesting pause…", "Pause requested."); }}>Request pause</button>}{actions.resume && <button className="secondary" aria-disabled={off || !!block} aria-describedby={block ? "setup-resume-note" : undefined} onClick={() => { if (!block) void control({ action: "pause", value: false }, "Requesting resume…", "Resume requested."); }}>Request resume</button>}<button className="secondary" aria-disabled={asked} onClick={() => { if (asked) return; setAsked(true); setRefreshNote(""); setNote(previous => previous?.error ? previous : null); void data.refresh(); }}>{asked ? "Refreshing…" : "Refresh progress"}</button></>}</div>
    {block && !row.runtime_ready && <p id="setup-resume-note" className="fine-print">{block}</p>}
    {orphan && <Recovery entry={entry} discard={() => { editors.discard(row.setup.id); requestAnimationFrame(() => document.querySelector<HTMLElement>(".setup-view h2")?.focus({ preventScroll: true })); }}/>}
  </>}</main>;
}

function BotRoute({ id, initialTab, data, back, hostChat, scrolls, disabled }: { id: string; initialTab: string; data: ReturnType<typeof useWorkspace>; back: () => void; hostChat: SetBotChatSlot; scrolls: Map<string, number>; disabled: boolean }) {
  const row = data.rows.find(item => item.setup.id === id), status = data.bots.find(item => item.bot_id === id);
  return status ? <BotWorkspace id={id} initialTab={initialTab} row={row} status={status} data={data} back={back} hostChat={hostChat} scrolls={scrolls} disabled={disabled}/> : <><header className="experience-header"><button className="icon" aria-label="Back" onClick={back}><ArrowLeft size={18}/></button><h1>Bot</h1></header><main className="content"><p>This bot’s runtime is not ready. Open its setup from Home.</p></main></>;
}
function BotWorkspace({ id, initialTab, row, status, data, back, hostChat, scrolls, disabled }: { id: string; initialTab: string; row?: api.SetupRecord; status: api.BotStatus; data: ReturnType<typeof useWorkspace>; back: () => void; hostChat: SetBotChatSlot; scrolls: Map<string, number>; disabled: boolean }) {
  const [tab, setTab] = useState(initialTab);
  const openTab = (next: string) => { setTab(next); requestAnimationFrame(() => document.getElementById(`workspace-tab-${next}`)?.focus()); };
  const chatSlot = useCallback((slot: HTMLDivElement | null) => hostChat(id, { slot }), [id, hostChat]);
  useEffect(() => hostChat(id, { hidden: tab !== "chat" }), [id, tab, hostChat]);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => setMenuOpen(false), [tab]);
  useEffect(() => setTab(initialTab), [initialTab]);
  const tabContent = useRef<HTMLElement>(null);
  useLayoutEffect(() => { if (tabContent.current) tabContent.current.scrollTop = scrolls.get(`${id}/${tab}`) ?? 0; }, [id, tab, scrolls]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const guard = useRef(false);
  const requests = useRequests(id);
  const [noticeTab, setNoticeTab] = useState(initialTab);
  const feedbackContext = useRef<{ key: string; value: string } | null>(null);
  const feedbackValue = (key: string) => key === "computer" ? status.computer : JSON.stringify(status.tasks.find(task => task.id === key));
  useEffect(() => {
    const context = feedbackContext.current;
    if (!busy && context && context.value !== feedbackValue(context.key)) { setError(""); feedbackContext.current = null; }
  }, [busy, status.computer, status.tasks]);
  useEffect(() => { if (!notice || busy) return; const timer = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(timer); }, [notice, busy]);
  const name = titleFor(row, status);
  const tabs = ["chat", "activity", "requests", "computer"];
  const count = requests.items.length + requests.secrets.length + (row?.setup.wait ? 1 : 0);
  const run = async (action: () => Promise<unknown>, pending = "Sending request…", confirmed?: string, context?: string) => {
    if (guard.current || disabled) return;
    guard.current = true; setBusy(true); setError(""); setNotice(pending); setNoticeTab(tab);
    feedbackContext.current = context ? { key: context, value: feedbackValue(context) } : null;
    try { await action(); if (confirmed) setNotice(confirmed); await data.refresh(); await requests.refresh(); }
    catch (e) { setNotice(""); setError(!(e instanceof api.ApiError) || e.status === 0 || e.status >= 500 ? "The response was not confirmed. The request may have applied. Refresh status before trying again." : e.message); }
    finally { guard.current = false; setBusy(false); }
  };
  const taskBlocked = (taskId: string) => disabled || busy || !requests.loaded || !!requests.error || requests.view.ops.has(taskKey(taskId)) || requests.view.review.has(taskKey(taskId));
  const taskFocus = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const old = taskFocus.current;
    if (!old) return;
    if (tab !== "activity" || (document.activeElement !== old && document.activeElement !== document.body)) { taskFocus.current = null; return; }
    if (!old.isConnected) {
      const target = tabContent.current?.querySelector<HTMLElement>("h2");
      if (target) { target.tabIndex = -1; target.focus({ preventScroll: true }); }
      taskFocus.current = null;
    }
  }, [status.tasks, tab]);
  const respondTask = async (taskId: string, label: string, action: () => Promise<unknown>) => {
    if (taskBlocked(taskId)) return;
    taskFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    await requests.store.perform({ identity: taskKey(taskId), label }, action);
    await data.refresh();
  };
  const privateScreen = !!row?.setup.wait || !!requests.secrets.length || !!requests.error || !requests.loaded;
  const queued = status.tasks.filter(task => ["queued", "held", "waiting"].includes(task.state));
  return <div className="bot-workspace">
    <header className="experience-header"><button className="icon" aria-label="Back" onClick={back}><ArrowLeft size={18}/></button><div className="header-title"><h1>{name}</h1><p role="status">{describeBot(status).label}</p></div><DropdownMenu.Root open={menuOpen} onOpenChange={setMenuOpen}><DropdownMenu.Trigger asChild><button className="icon" aria-label="Bot controls"><MoreHorizontal size={20}/></button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="start-menu bot-menu" align="end" sideOffset={8} collisionPadding={12}>
      <DropdownMenu.Item className="start-menu-item" disabled={disabled || busy} onSelect={() => { void run(() => api.botPause(!status.automation_paused, id), status.automation_paused ? "Requesting resume…" : "Requesting pause…", status.automation_paused ? "Resume requested." : "Pause requested."); }}>{status.automation_paused ? "Resume automation" : "Pause automation"}</DropdownMenu.Item>
      <DropdownMenu.Label className="menu-note">Pause prevents new tasks. Current work can continue.</DropdownMenu.Label>
      <DropdownMenu.Item className="start-menu-item" disabled={disabled || busy} onSelect={() => { void run(async () => { const result = await api.botStop(id); setNotice(result.unconfirmed.length ? `Stop requested. ${result.unconfirmed.length} command outcomes remain unconfirmed. Check Requests.` : "Stop confirmed"); }); }}>Stop work</DropdownMenu.Item>
      <DropdownMenu.Item className="start-menu-item" disabled={disabled || busy || status.computer === "waking" || status.computer === "saving"} onSelect={() => { void run(() => status.computer === "awake" ? api.botSleep(id) : api.botWake(id), status.computer === "awake" ? "Requesting sleep…" : "Requesting wake…", status.computer === "awake" ? "Sleep requested." : "Wake requested.", "computer"); }}>{status.computer === "awake" ? "Sleep computer" : "Wake computer"}</DropdownMenu.Item>
    </DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root></header>
    <div className="workspace-tabs" role="tablist" aria-label="Bot workspace">{tabs.map((value, index) => <button key={value} id={`workspace-tab-${value}`} role="tab" aria-selected={tab === value} aria-controls={`workspace-panel-${value}`} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)} onKeyDown={event => { const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1; if (next >= 0) { event.preventDefault(); setTab(tabs[next]); document.getElementById(`workspace-tab-${tabs[next]}`)?.focus(); } }}>{value}{value === "requests" && count > 0 && <span>{count}</span>}{tab === value && <motion.span layoutId="active-workspace-tab" className="tab-underline"/>}</button>)}</div>
    {data.error && <p className="workspace-connection" role="alert">Connection lost. Showing saved status.<button className="text-button" onClick={() => { void data.refresh(); }}>Refresh</button></p>}
    {tab === noticeTab && (notice || error) && <p className={error ? "error-note" : "inline-status"} role={error ? "alert" : "status"}>{error || notice}</p>}
    {count > 0 && tab === "chat" && <button className="request-banner" onClick={() => openTab("requests")}>{count} request{count === 1 ? "" : "s"} need your attention <ChevronRight size={18}/></button>}
    <div ref={chatSlot} className="chat-tab" role="tabpanel" id="workspace-panel-chat" aria-labelledby="workspace-tab-chat" hidden={tab !== "chat"}/>
    {tab !== "chat" && <main key={tab} className="content" role="tabpanel" id={`workspace-panel-${tab}`} aria-labelledby={`workspace-tab-${tab}`} tabIndex={0} ref={tabContent} onScroll={event => scrolls.set(`${id}/${tab}`, event.currentTarget.scrollTop)}>
      {tab === "activity" && <><h2>Tasks</h2><p className="activity-response" role="status">{requests.view.notice.tone === "error" && !requests.view.ops.size && !status.tasks.some(task => ["queued", "held", "waiting"].includes(task.state) && requests.view.failures.has(taskKey(task.id))) ? "" : requests.view.notice.text}</p>{!status.tasks.length && <p className="empty-result">No tasks yet</p>}<ul className="task-list">{status.tasks.map(task => <li key={task.id}><div><strong>{task.title}</strong><span className="task-state">{{ queued: "Queued", held: "On hold", waiting: "Waiting", running: "Working", done: "Completed", failed: "Failed", cancelled: "Cancelled" }[task.state] ?? task.state}</span></div>{task.detail && <p>{task.detail}</p>}{requests.view.ops.get(taskKey(task.id)) && <p role="status">{requests.view.ops.get(taskKey(task.id))!.message}</p>}{["queued", "held", "waiting"].includes(task.state) && requests.view.failures.get(taskKey(task.id)) && <p role="alert">{requests.view.failures.get(taskKey(task.id))}</p>}{(requests.view.ops.has(taskKey(task.id)) || requests.view.review.has(taskKey(task.id))) && <button className="text-button" onClick={() => openTab("requests")}>Review response in Requests</button>}{["queued", "held", "waiting"].includes(task.state) && <div className="task-controls">{task.state === "held" && <button className="text-button" aria-disabled={taskBlocked(task.id)} onClick={() => { void respondTask(task.id, `Retry ${task.title}`, () => api.botRetryTask(task.id, id)); }}>Retry</button>}{task.state !== "failed" && <button className="text-button" aria-disabled={taskBlocked(task.id)} onClick={() => { void respondTask(task.id, `Cancel ${task.title}`, () => api.botCancelTask(task.id, id)); }}>Cancel</button>}{["queued", "held"].includes(task.state) && <select aria-label={`Priority for ${task.title}`} value={task.priority} aria-disabled={taskBlocked(task.id)} onChange={event => { if (taskBlocked(task.id)) return; void run(() => api.botPriority(task.id, Number(event.target.value), id), `Updating priority for ${task.title}…`, `Priority change accepted for ${task.title}.`, task.id); }}><option value={0}>Low priority</option><option value={1}>Normal priority</option><option value={2}>High priority</option></select>}</div>}{task.state === "done" && <button className="text-button" onClick={() => openTab("chat")}>Open conversation</button>}</li>)}</ul>{queued.length > 0 && <p className="fine-print">Queued work starts only when the server’s pause, permission and spending checks allow it.</p>}{row && <details className="advanced"><summary>Approved setup</summary><p>{row.setup.draft.instructions}</p><p>{row.setup.draft.authority === "Full" ? "Full authority within this job" : row.setup.draft.authority.Restricted.join(", ") || "No actions granted"}</p>{row.setup.draft.budget && <p>Reviewed ceiling: {money(row.setup.draft.budget.ceiling, row.setup.draft.budget.exponent, row.setup.draft.budget.currency) ?? "Unavailable"}. Allowance: {money(row.setup.draft.budget.allowance, row.setup.draft.budget.exponent, row.setup.draft.budget.currency) ?? "Unavailable"} per {row.setup.draft.budget.period}.</p>}</details>}</>}
      {tab === "requests" && <Requests id={id} row={row} protectedInput={data.protectedInput} requests={requests} disabled={disabled} refresh={data.refresh}/>}
      {tab === "computer" && <><div className="computer-toolbar"><span>{{ saved: "Asleep", waking: "Waking up", awake: "Awake", saving: "Saving and sleeping", failed: "Computer needs attention" }[status.computer]}</span><button className="secondary" aria-disabled={disabled || busy || status.computer === "saving" || status.computer === "waking"} onClick={() => { if (status.computer === "saving" || status.computer === "waking") return; void run(() => status.computer === "awake" ? api.botSleep(id) : api.botWake(id), status.computer === "awake" ? "Requesting sleep…" : "Requesting wake…", status.computer === "awake" ? "Sleep requested." : "Wake requested.", "computer"); }}>{status.computer === "awake" ? "Sleep" : "Wake"}</button></div>{disabled ? <p className="empty-result">Refresh the connection before viewing the computer.</p> : privateScreen ? <div className="live-screen"><p>{!requests.loaded ? "Checking whether the screen can be shown…" : requests.error ? "Screen hidden because privacy status could not be checked." : "Screen hidden while sign-in or protected input needs attention"}</p><button className="secondary" onClick={() => openTab("requests")}>Open requests</button></div> : status.computer === "awake" ? <Computer key={id} id={id}/> : <div className="live-screen"><Monitor size={28}/><p>{status.computer === "saved" ? "The computer is asleep. Wake it to see the screen." : status.computer === "saving" ? "Saving the computer before it sleeps." : status.computer === "failed" ? "The computer could not start. You can request wake again." : "Waking the computer. The screen will appear when it is ready."}</p></div>}</>}
    </main>}
  </div>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><RequestsProvider><App/></RequestsProvider></StrictMode>);
