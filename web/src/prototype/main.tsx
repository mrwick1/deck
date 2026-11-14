import { StrictMode, useReducer, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion, MotionConfig, type AnimationDefinition } from "motion/react";
import { ArrowUp, ArrowUpRight, ChevronLeft, Plus, House, Mail, Check, Monitor, MessageCircle, Activity, ArrowRight, Pause, Play, RotateCcw, Command } from "lucide-react";
import { labels, type Action } from "./journey";
import { flow, initialFlow, validProposal, type Proposal } from "./flow";
import { HomeStart } from "../experience/HomeStart";
import "./style.css";
import "../experience/compact.css";

const blankProposal: Proposal = { name: "Morning mail", job: "", authority: "full", permissions: ["Read mail", "Organize mail"], ceiling: "" };
const tabs = [{ id: "chat", Icon: MessageCircle }, { id: "activity", Icon: Activity }, { id: "computer", Icon: Monitor }];
function App() {
  const [state, dispatch] = useReducer(flow, initialFlow);
  const [tab, setTab] = useState("chat");
  const [homeChat, setHomeChat] = useState(false);
  const [setup, setSetup] = useState<"describe" | "review">("describe");
  const [proposal, setProposal] = useState<Proposal>(blankProposal);
  const [offline, setOffline] = useState(false);
  const [mode, setMode] = useState<"guide" | "queue">("guide");
  const [notice, setNotice] = useState("");
  const [details, setDetails] = useState(false);
  const [destination, setDestination] = useState("new");
  const [homeMessages, setHomeMessages] = useState<string[]>([]);
  const scrollPositions = useRef<Record<string, number>>({});
  const heading = useRef<HTMLHeadingElement>(null);
  const navigated = useRef(false);
  const bot = state.bots.find(item => item.id === state.view);
  const workspace = !!bot;
  const firstHome = !workspace && !homeChat && !state.bots.length && setup === "describe";
  const phase = bot?.phase ?? "idle";
  const scope = bot?.id ?? "home";
  const draft = state.drafts[scope] ?? "";
  const needs = state.bots.filter(item => ["connect", "phone", "failed"].includes(item.phase));
  const viewKey = bot ? `${bot.id}-${tab}` : homeChat ? "home-chat" : "home";
  const open = (id: string) => { navigated.current = true; setNotice(""); dispatch({ type: "navigate", view: id }); setTab("chat"); };
  const home = () => { navigated.current = true; dispatch({ type: "navigate", view: "home" }); setNotice(""); };
  const focusPage = (animation: AnimationDefinition) => { if (typeof animation === "object" && "opacity" in animation && animation.opacity === 1 && navigated.current) { heading.current?.focus({ preventScroll: true }); navigated.current = false; } };
  const scrollProps = (key: string) => ({
    onScroll: (event: React.UIEvent<HTMLElement>) => { scrollPositions.current[key] = event.currentTarget.scrollTop; },
    ref: (element: HTMLElement | null) => { if (element) element.scrollTop = scrollPositions.current[key] ?? 0; },
  });
  const startSetup = () => { home(); setHomeChat(true); setDestination("new"); };
  const act = (action: Action) => { if (bot && !offline) dispatch({ type: "act", id: bot.id, action }); };
  const submit = () => {
    const text = draft.trim();
    if (!text || offline) return;
    if (bot) {
      dispatch({ type: "message", id: bot.id, text, mode });
      setNotice(mode === "queue" ? "Task added to the queue" : "Instruction added to this bot’s chat");
    } else if (destination !== "new" && state.bots.some(item => item.id === destination)) {
      dispatch({ type: "message", id: destination, text, mode: "queue" });
      setHomeMessages(items => [...items, text]);
      setHomeChat(true);
      setNotice(`Task queued for ${state.bots.find(item => item.id === destination)?.name}`);
    } else {
      navigated.current = true; setProposal(current => ({ ...current, job: text })); setSetup("review"); setHomeChat(true);
    }
    dispatch({ type: "draft", scope, text: "" });
  };
  const create = () => {
    if (!validProposal(proposal) || offline) return;
    dispatch({ type: "create", proposal }); setTab("chat"); setNotice("");
    setSetup("describe"); setProposal(blankProposal); navigated.current = true;
  };
  const tabKey = (event: React.KeyboardEvent, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault(); setTab(tabs[next].id); document.getElementById(`tab-${tabs[next].id}`)?.focus();
  };
  const controls = <div className="actions">
    {phase === "working" && <button className="secondary" disabled={offline} onClick={() => act("pause")}><Pause size={18}/> Pause</button>}
    {phase === "paused" && <button className="primary" disabled={offline} onClick={() => act("resume")}><Play size={18}/> Resume</button>}
  </div>;
  const review = <div className="journey-card">
    <span className="eyebrow">Setup review</span><h2>A bot for this job</h2>
    <label>Bot name<input value={proposal.name} maxLength={60} onChange={e => setProposal({ ...proposal, name: e.target.value })}/></label>
    <label>Its job<textarea value={proposal.job} maxLength={2000} onChange={e => setProposal({ ...proposal, job: e.target.value })}/></label>
    <fieldset className="permission-options"><legend>Authority for this job</legend>
      <label><input type="radio" name="authority" checked={proposal.authority === "full"} onChange={() => setProposal({ ...proposal, authority: "full" })}/><span>Full authority for this job<small>Includes sending, posting and deleting within this job</small></span></label>
      <label><input type="radio" name="authority" checked={proposal.authority === "restricted"} onChange={() => setProposal({ ...proposal, authority: "restricted" })}/><span>Only the actions I select</span></label>
      {proposal.authority === "restricted" && <div className="permissions">{["Read mail", "Organize mail", "Send mail", "Delete mail"].map(permission => <label key={permission}><input type="checkbox" checked={proposal.permissions.includes(permission)} onChange={e => setProposal({ ...proposal, permissions: e.target.checked ? [...proposal.permissions, permission] : proposal.permissions.filter(p => p !== permission) })}/>{permission}</label>)}</div>}
    </fieldset>
    <label>Spending ceiling for this demo (USD)<input inputMode="decimal" value={proposal.ceiling} onChange={e => setProposal({ ...proposal, ceiling: e.target.value })} placeholder="Choose an amount, including 0" aria-describedby="ceiling-help"/></label>
    <p id="ceiling-help" className="fine-print">Choose the total allowance for this prototype session. No money is spent. Live accounting settings will come from your configured model.</p>
    <div className="review-row"><span>First task</span><strong>Your job description above</strong></div>
    <p className="fine-print">Sample mail workflow only. This prototype does not connect real accounts or execute arbitrary jobs.</p>
    <div className="review-actions"><button className="secondary" onClick={() => { setSetup("describe"); dispatch({ type: "draft", scope: "home", text: proposal.job }); }}>Back to description</button><button className="primary" disabled={!validProposal(proposal) || offline} onClick={create}>Create bot <ArrowRight size={18}/></button></div>
  </div>;
  return <MotionConfig reducedMotion="user" transition={{ type: "spring", stiffness: 380, damping: 38 }}>
    <div className="shell compact">
      <aside className="rail"><button className="brand" onClick={() => { home(); setHomeChat(false); }} aria-label="Deck home"><Command size={24}/></button><button className={!workspace && !homeChat ? "selected" : ""} aria-label="Home" onClick={() => { home(); setHomeChat(false); }}><House size={18}/></button><button className={!workspace && homeChat ? "selected" : ""} aria-label="Chat with Deck" onClick={() => { home(); setHomeChat(true); }}><MessageCircle size={18}/></button><span className="rail-spacer"/><span className="avatar">You</span></aside>
      <div className="app">{firstHome ? <HomeStart sample draft={draft} setDraft={text => dispatch({ type: "draft", scope: "home", text })} submit={submit} disabled={offline} notice={offline ? "Offline · Your draft is kept" : notice} actions={[{ label: offline ? "Restore connection" : "Simulate offline", run: () => setOffline(!offline) }, { label: "About this preview", run: () => setNotice("Sample data only. Progress resets when you reload.") }]} /> : <>
        {!workspace && <header><button className="wordmark" onClick={() => { home(); setHomeChat(false); }}>deck<span> / {homeChat ? "Chat" : "Home"}</span></button><span className="preview-label">Prototype · sample data</span><button className="icon" aria-label="Reset preview" onClick={() => { dispatch({ type: "reset" }); setHomeChat(false); setProposal(blankProposal); setSetup("describe"); setHomeMessages([]); setNotice(""); setDestination("new"); scrollPositions.current = {}; setOffline(false); }}><RotateCcw size={18}/></button></header>}
        <div className="connection" role="status">{offline ? "Offline · Your draft and progress are kept here" : notice}</div>
        <AnimatePresence mode="wait" initial={false}>
          {!workspace ? <motion.main onAnimationComplete={focusPage} key={homeChat ? "home-chat" : "home"} className="home" {...scrollProps(viewKey)} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
            {homeChat ? <div className="home-conversation">
              <button className="text-button" onClick={() => { setHomeChat(false); navigated.current = true; }}><ChevronLeft size={18}/> Your workspace</button>
              <h1 ref={heading} tabIndex={-1}>Chat with Deck</h1><p className="chat-description">Create a bot or send a task to one you already have</p>
              {homeMessages.map((message, index) => <div className="user-message" key={index}>{message}</div>)}
              {setup === "review" ? <><div className="user-message">{proposal.job}</div>{review}</> : <div className="journey-card"><h2>What should your bot take care of?</h2><p>Describe the job below. You’ll review its name, authority and spending ceiling before creating it.</p><button className="suggestion" onClick={() => { setProposal({ ...blankProposal, job: "Read my inbox each morning and summarize messages that need a reply." }); setSetup("review"); }}>Try a morning mail bot <ArrowRight size={18}/></button></div>}
            </div> : <>
              <div className="greeting"><h1 ref={heading} tabIndex={-1}>Your workspace</h1><p>Tasks, requests and results from your bots</p></div>
              <section className="attention-slot" aria-label="Needs you">{needs.length ? <button className="attention" onClick={() => open(needs[0].id)}><span className="status-dot amber"/><span><strong>{labels[needs[0].phase]}</strong><small>{needs[0].name}{needs.length > 1 ? ` · ${needs.length} bots need you` : " · Continue where you left off"}</small></span><ArrowRight size={18}/></button> : <div className="all-clear"><Check size={18}/> No requests waiting for you</div>}</section>
              <section><div className="section-heading"><h2>Your bots</h2><button className="text-button" onClick={startSetup}><Plus size={18}/> New bot</button></div><div className="bot-grid">
                {state.bots.map(item => <button className="bot-card" key={item.id} onClick={() => open(item.id)}><span className="bot-icon coral"><Mail size={18}/></span><span className="card-arrow"><ArrowUpRight size={18}/></span><h3>{item.name}</h3><p className="bot-job">{item.job}</p><span className="bot-status"><span className={`status-dot ${needs.some(n => n.id === item.id) ? "amber" : ""}`}/>{labels[item.phase]}</span>{item.queue.length > 0 && <small>{item.queue.length} queued</small>}</button>)}
                <button className="bot-card new-card" onClick={startSetup}><span className="plus-circle"><Plus size={24}/></span><h3>{state.bots.length ? "Create another bot" : "Create your first bot"}</h3><p>Start with a job you want done</p></button>
              </div></section>
              {setup === "review" && <button className="saved-review" onClick={() => { setHomeChat(true); navigated.current = true; }}><span><strong>Continue setup</strong><small>{proposal.name} · Your review is saved in this session</small></span><ArrowRight size={18}/></button>}
              <section className="results"><div className="section-heading"><h2>Recent results</h2><span>Completed work</span></div>{state.bots.some(item => item.results.length) ? state.bots.flatMap(item => item.results.map((task, index) => <button className="result-row" key={`${item.id}-${index}`} onClick={() => { open(item.id); setTab("activity"); }}><span className="result-icon"><Mail size={18}/></span><span><strong>{task}</strong><small>{item.name} · Sample result</small></span><ArrowUpRight size={18}/></button>)) : <div className="empty-result">No results yet. Set up your first bot to get started.</div>}</section>
            </>}
          </motion.main> : <motion.main onAnimationComplete={focusPage} key={bot.id} className="workspace" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 8 }}>
            <div className="workspace-heading"><button className="icon" aria-label="Back to home" onClick={() => { home(); setHomeChat(false); }}><ChevronLeft size={18}/></button><span className="bot-icon coral"><Mail size={18}/></span><div><h1 ref={heading} tabIndex={-1}>{bot.name}</h1><p role="status">{labels[phase]}</p></div>{controls}</div>
            <div className="tabs" role="tablist" aria-label="Bot workspace">{tabs.map(({ id, Icon }, index) => <button role="tab" id={`tab-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} aria-controls={`panel-${id}`} key={id} onKeyDown={event => tabKey(event, index)} onClick={() => setTab(id)}>{tab === id && <motion.span layoutId="tab" className="tab-highlight"/>}<Icon size={18}/><span>{id}</span></button>)}</div>
            <div key={viewKey} {...scrollProps(viewKey)} className="workspace-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0}>
              {tab === "chat" && <div className="conversation"><details className="task-context"><summary>Current task</summary><p>{bot.task}</p></details><div className="journey-card">
                {phase === "connect" && <><span className="eyebrow">Account connection</span><h2>Connect Gmail</h2><p>Your setup is saved. This preview uses a simulated connection. Don’t enter real credentials.</p><div className="connection-tile"><Mail size={18}/><span>Gmail<small>Demo mail workspace</small></span><span className="badge">Demo</span></div><button className="primary" disabled={offline} onClick={() => act("connect")}>Try demo connection <ArrowRight size={18}/></button></>}
                {phase === "phone" && <><span className="eyebrow">Sign-in approval</span><h2>Approve the sign-in</h2><p>Match the number on your phone. You can return home and come back to this step.</p><div className="phone-number">42</div><p className="fine-print">Simulated challenge. No phone notification was sent.</p><button className="primary" disabled={offline} onClick={() => act("approve")}>Simulate verified approval <Check size={18}/></button></>}
                {phase === "working" && <><span className="eyebrow">In progress</span><h2>Preparing your mail brief</h2><p>Leave instructions here or queue another task. You can return home while work continues.</p><ul className="checklist"><li><Check size={18}/> Demo account connected</li><li><Check size={18}/> Setup approved</li><li><span className="status-dot"/> Preparing a sample result</li></ul></>}
                {phase === "paused" && <><h2>Work paused</h2><p>Your current task and queue are kept. Resume from the control above when you’re ready.</p></>}
                {phase === "failed" && <><span className="eyebrow">Connection issue</span><h2>Connection interrupted</h2><p>Your progress and queued tasks are still here. Reconnect to continue.</p><button className="primary" disabled={offline} onClick={() => act("retry")}>Reconnect and continue <ArrowRight size={18}/></button></>}
                {phase === "done" && <><span className="eyebrow">Completed · sample result</span><h2>Morning mail brief</h2><p>Three updates from the sample inbox</p><ol className="brief"><li><strong>A decision for today</strong><span>The project proposal is ready for your review.</span></li><li><strong>Your afternoon is clear</strong><span>The planning meeting moved to tomorrow.</span></li><li><strong>Something worth reading</strong><span>Your weekly reading list is ready.</span></li></ol><button className="secondary" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? "Hide" : "View"} work details</button>{details && <p className="fine-print">A sample result for: {bot.task}. No real inbox was read or changed.</p>}</>}
              </div>
              {!!bot.queue.length && <div className="queue-card"><h2>Up next <span>{bot.queue.length}</span></h2><ol>{bot.queue.map((task, index) => <li key={index}>{task}</li>)}</ol>{phase === "done" && <button className="secondary" disabled={offline} onClick={() => dispatch({ type: "start-next", id: bot.id })}>Start next demo task <Play size={18}/></button>}</div>}
              {bot.messages.map((message, index) => <div className="user-message" key={index}><small>{message.mode === "queue" ? "Queued task" : "Instruction"}</small>{message.text}</div>)}
              {!!bot.messages.length && <p className="fine-print">Messages are stored in this preview session. No model is connected.</p>}
              </div>}
              {tab === "activity" && <div className="activity-panel"><span className="eyebrow">Task activity</span><h2>Progress</h2><ol className="timeline">{bot.history.map((entry, index) => <li key={index}><Check size={18}/><span>{entry}</span></li>)}</ol><h2>Completed tasks</h2>{bot.results.length ? bot.results.map((result, index) => <div className="activity-result" key={index}><strong>{result}</strong><p>Sample output: a proposal needs review, a meeting moved, and a reading list is ready. No real mail was read.</p></div>) : <p>No completed tasks yet</p>}<div className="settings-summary"><h2>Approved setup</h2><p>{bot.authority === "full" ? "Full authority within this job" : bot.permissions.join(", ") || "No actions granted"}</p><p>Demo ceiling: USD {Number(bot.ceiling).toFixed(2)}</p></div></div>}
              {tab === "computer" && <div className="computer-panel"><Monitor size={32}/><h2>{phase === "phone" || phase === "connect" ? "Screen hidden during sign-in" : "Bot computer"}</h2><p>{phase === "phone" || phase === "connect" ? "The live screen stays hidden while sensitive information is entered." : "The bot’s live computer will appear here when connected."}</p><span className="badge">Preview · no live computer</span></div>}
            </div>
          </motion.main>}
        </AnimatePresence>
        <footer className="composer-area">{(!workspace || tab === "chat") && !(homeChat && setup === "review" && !workspace) && <form className="composer" onSubmit={e => { e.preventDefault(); submit(); }}><div className="composer-meta"><span>{bot ? `To ${bot.name}` : "To Deck"}</span>{bot ? <select aria-label="Message intent" value={mode} onChange={e => setMode(e.target.value as "guide" | "queue")}><option value="guide">Guide this task</option><option value="queue">Add a task</option></select> : <select aria-label="Task destination" value={destination} onChange={e => setDestination(e.target.value)}><option value="new">Create a bot</option>{state.bots.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>}</div><div className="composer-input"><textarea aria-label={bot ? `Message ${bot.name}` : "Message Deck"} placeholder={bot ? mode === "queue" ? "Describe the next task" : "Guide this task" : destination === "new" ? "Describe your bot’s job" : "Describe a task"} value={draft} rows={1} onChange={e => dispatch({ type: "draft", scope, text: e.target.value })}/><button className="send" aria-label="Send message" disabled={!draft.trim() || offline}><ArrowUp size={18}/></button></div></form>}<details className="preview-disclosure"><summary>Prototype · sample data · controls</summary><div className="preview-controls"><span>Try the journey</span><button onClick={() => setOffline(!offline)}>{offline ? "Restore connection" : "Go offline"}</button>{phase === "working" && <><button disabled={offline} onClick={() => act("fail")}>Simulate failure</button><button disabled={offline} onClick={() => act("finish")}>Finish task</button></>}<span className="local-only">Demo state resets on reload</span></div></details></footer>
      </>}</div>
    </div>
  </MotionConfig>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><App/></StrictMode>);
