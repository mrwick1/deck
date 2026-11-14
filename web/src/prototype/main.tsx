import { StrictMode, useReducer, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { ArrowUp, ArrowUpRight, ChevronLeft, Plus, House, Mail, Check, Monitor, MessageCircle, Activity, ArrowRight, Pause, Play, RotateCcw, Command } from "lucide-react";
import { journey, labels, type Action } from "./journey";
import "./style.css";

function App() {
  const [phase, dispatch] = useReducer(journey, "idle");
  const [workspace, setWorkspace] = useState(false);
  const [tab, setTab] = useState("chat");
  const [draft, setDraft] = useState("");
  const [job, setJob] = useState("Organize my morning email and prepare a short daily brief.");
  const [name, setName] = useState("Morning mail");
  const [messages, setMessages] = useState<string[]>([]);
  const [offline, setOffline] = useState(false);
  const [details, setDetails] = useState(false);
  const [mode, setMode] = useState("Guide this task");
  const act = (action: Action) => dispatch(action);
  const open = () => { setWorkspace(true); setTab("chat"); };
  const propose = () => { if (draft.trim()) setJob(draft.trim()); setDraft(""); act("propose"); open(); };
  const needsYou = ["review", "connect", "phone", "failed"].includes(phase);
  const submit = () => { if (!draft.trim() || offline) return; if (phase === "idle") { propose(); return; } setMessages(m => [...m, draft.trim()]); setDraft(""); open(); };
  const control = <div className="actions">{phase === "working" && <button className="secondary" onClick={() => act("pause")}><Pause size={18}/> Pause work</button>}{phase === "paused" && <button className="primary" onClick={() => act("resume")}><Play size={18}/> Resume work</button>}</div>;
  return <MotionConfig reducedMotion="user" transition={{ type: "spring", stiffness: 380, damping: 38 }}>
    <div className="shell">
      <aside className="rail"><a href="#" className="brand" onClick={e => { e.preventDefault(); setWorkspace(false); }} aria-label="Deck home"><Command size={24}/></a><button className={!workspace ? "selected" : ""} aria-label="Home" onClick={() => setWorkspace(false)}><House size={18}/></button><button aria-label="Open bot chat" onClick={open}><MessageCircle size={18}/></button><span className="rail-spacer"/><span className="avatar">You</span></aside>
      <div className="app">
        <header><button className="wordmark" onClick={() => setWorkspace(false)}>deck<span> / {workspace ? name : "Home"}</span></button><span className="preview-label">Prototype · sample data</span><button className="icon" aria-label="Reset preview" onClick={() => { act("reset"); setWorkspace(false); setMessages([]); setDraft(""); setOffline(false); }}><RotateCcw size={18}/></button></header>
        <div className="connection" role="status">{offline ? "Reconnecting… Your draft and workspace are kept here." : ""}</div>
        <AnimatePresence mode="wait" initial={false}>
          {!workspace ? <motion.main key="home" className="home" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
            <div className="greeting"><h1>Your workspace</h1><p>Tasks, requests and results from your bots</p></div>
            <section className="attention-slot" aria-label="Needs you">{needsYou ? <button className="attention" onClick={open}><span className="status-dot amber"/><span><strong>{labels[phase]}</strong><small>{name} · A quick step from you</small></span><ArrowRight size={18}/></button> : <div className="all-clear"><Check size={18}/> No requests waiting for you</div>}</section>
            <section><div className="section-heading"><h2>Your bots</h2><button className="text-button" onClick={propose}><Plus size={18}/> New bot</button></div><div className="bot-grid"><button className="bot-card" onClick={open}><span className="bot-icon coral"><Mail size={18}/></span><span className="card-arrow"><ArrowUpRight size={18}/></span><h3>{name}</h3><p>Read, organize and summarize your inbox</p><span className="bot-status"><span className={`status-dot ${needsYou ? "amber" : ""}`}/>{labels[phase]}</span></button><button className="bot-card new-card" onClick={propose}><span className="plus-circle"><Plus size={24}/></span><h3>Create another bot</h3><p>Start with a job you want done</p></button></div></section>
            <section className="results"><div className="section-heading"><h2>Recent results</h2><span>Completed work</span></div>{phase === "done" ? <button className="result-row" onClick={open}><span className="result-icon"><Mail size={18}/></span><span><strong>Morning mail brief</strong><small>Morning mail · Just now · Sample brief</small></span><ArrowUpRight size={18}/></button> : <div className="empty-result">No results yet. Set up your first bot to get started.</div>}</section>
          </motion.main> : <motion.main key="workspace" className="workspace" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 8 }}>
            <div className="workspace-heading"><button className="icon" aria-label="Back to home" onClick={() => setWorkspace(false)}><ChevronLeft/></button><span className="bot-icon coral"><Mail size={18}/></span><div><h1>{name}</h1><p role="status">{labels[phase]}</p></div>{control}</div>
            <div className="tabs" role="tablist" aria-label="Bot workspace">{[["chat", MessageCircle], ["activity", Activity], ["computer", Monitor]].map(([id, Icon]) => { const value = id as string; const Glyph = Icon as typeof Mail; return <button role="tab" id={`tab-${value}`} aria-selected={tab === value} aria-controls={`panel-${value}`} key={value} onClick={() => setTab(value)}>{tab === value && <motion.span layoutId="tab" className="tab-highlight"/>}<Glyph size={18}/><span>{value}</span></button>; })}</div>
            <div className="workspace-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
              <div hidden={tab !== "chat"} className="conversation">
                <div className="intro"><p>Use this chat to set up your bot and direct its work</p></div>
                {phase === "idle" ? <div className="journey-card"><h2>Meet your morning mail bot</h2><p>Turn a busy inbox into a short, useful brief. Start by reviewing what it will do.</p><button className="primary" onClick={propose}>Set up this bot <ArrowRight size={18}/></button></div> : <><div className="user-message">{job}</div><div className="journey-card">
                  {phase === "review" && <><span className="eyebrow">Setup review</span><h2>Review your mail bot</h2><label>Bot name<input value={name} maxLength={60} onChange={e => setName(e.target.value)}/></label><label>Its job<textarea value={job} onChange={e => setJob(e.target.value)}/></label><div className="review-row"><span>Permissions</span><strong>Read and organize mail</strong></div><div className="review-row"><span>Account</span><strong>Connect after review</strong></div><p className="fine-print">Preview only. No account is connected, money spent, or message sent.</p><button className="primary" onClick={() => act("create")}>Create bot <ArrowRight size={18}/></button></>}
                  {phase === "connect" && <><span className="eyebrow">Account connection</span><h2>Connect Gmail</h2><p>In the finished app, credentials go through protected input. This preview uses a simulated connection. Don’t enter real credentials.</p><div className="connection-tile"><Mail size={18}/><span>Gmail<small>Your private mail workspace</small></span><span className="badge">Demo</span></div><button className="primary" onClick={() => act("connect")}>Try demo connection <ArrowRight size={18}/></button></>}
                  {phase === "phone" && <><span className="eyebrow">Sign-in approval</span><h2>Approve the sign-in</h2><p>Approve the sign-in request. You can leave this workspace and come back to the same step.</p><div className="phone-number">42</div><p className="fine-print">Simulated challenge. No phone notification was sent.</p><button className="primary" onClick={() => act("approve")}>Simulate verified approval <Check size={18}/></button></>}
                  {phase === "working" && <><span className="eyebrow">In progress</span><h2>Preparing your mail brief</h2><p>Your bot is preparing a concise morning brief. You can keep chatting or head back home.</p><ul className="checklist"><li><Check size={18}/> Account connected</li><li><Check size={18}/> Preferences saved</li><li><span className="status-dot"/> Preparing your brief</li></ul></>}
                  {phase === "paused" && <><h2>Work paused</h2><p>Your work is paused. Resume when you’re ready.</p>{control}</>}
                  {phase === "failed" && <><span className="eyebrow">Connection issue</span><h2>Connection interrupted</h2><p>Your progress is still here. Try reconnecting to continue preparing the brief.</p><button className="primary" onClick={() => act("retry")}>Reconnect and continue <ArrowRight size={18}/></button></>}
                  {phase === "done" && <><span className="eyebrow">Completed · sample result</span><h2>Morning mail brief</h2><p>Three updates from your inbox</p><ol className="brief"><li><strong>A decision for today</strong><span>The project proposal is ready for your review.</span></li><li><strong>Your afternoon is clear</strong><span>The planning meeting moved to tomorrow.</span></li><li><strong>Something worth reading</strong><span>Your weekly reading list is ready.</span></li></ol><button className="secondary" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? "Hide" : "View"} work details</button>{details && <p className="fine-print">This is a sample result. No real inbox was read or changed.</p>}</>}
                </div></>}
                {messages.map((message, i) => <div className="user-message" key={i}>{message}</div>)}{messages.length > 0 && <p className="fine-print">Message kept in this preview. Live bot responses will be connected later.</p>}
              </div>
              <div hidden={tab !== "activity"} className="activity-panel"><span className="eyebrow">Task activity</span><h2>Progress</h2><div className="timeline"><p><Check size={18}/> Workspace opened</p><p><Activity size={18}/>{labels[phase]}</p></div><p className="fine-print">This preview shows sample activity, not a live execution log.</p></div>
              <div hidden={tab !== "computer"} className="computer-panel"><Monitor size={38}/><h2>{phase === "phone" || phase === "connect" ? "Screen hidden during sign-in" : "Bot computer"}</h2><p>{phase === "phone" || phase === "connect" ? "The live screen stays hidden while sensitive information is entered." : "The bot’s live computer will appear here when connected."}</p><span className="badge">Preview · no live computer</span></div>
            </div>
          </motion.main>}
        </AnimatePresence>
        <footer className="composer-area"><form className="composer" onSubmit={e => { e.preventDefault(); submit(); }}><div className="composer-meta"><span>{workspace ? name : "To Deck · All bots"}</span>{workspace && <select aria-label="Message intent" value={mode} onChange={e => setMode(e.target.value)}><option>Guide this task</option><option>Add a task</option></select>}</div><div className="composer-input"><textarea aria-label={workspace ? `Message ${name}` : "Message Deck"} placeholder={workspace ? "Message your bot" : "Describe a task"} value={draft} rows={1} onChange={e => setDraft(e.target.value)}/><button className="send" aria-label="Send message" disabled={!draft.trim() || offline}><ArrowUp size={18}/></button></div></form><div className="preview-controls"><span>Try the journey</span><button onClick={() => setOffline(!offline)}>{offline ? "Restore connection" : "Go offline"}</button>{phase === "working" && <><button onClick={() => act("fail")}>Simulate failure</button><button onClick={() => act("finish")}>Finish task</button></>}<span className="local-only">Demo state resets on reload</span></div></footer>
      </div>
    </div>
  </MotionConfig>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><App/></StrictMode>);
