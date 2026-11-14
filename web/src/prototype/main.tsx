import { StrictMode, useReducer, useState } from "react";
import { createRoot } from "react-dom/client";
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { ArrowUp, ArrowUpRight, ChevronLeft, Plus, Sparkles, Mail, Check, Monitor, MessageCircle, Activity, ArrowRight, Pause, Play, RotateCcw, Command } from "lucide-react";
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
  const control = <div className="actions">{phase === "working" && <button className="secondary" onClick={() => act("pause")}><Pause size={15}/> Pause work</button>}{phase === "paused" && <button className="primary" onClick={() => act("resume")}><Play size={15}/> Resume work</button>}</div>;
  return <MotionConfig reducedMotion="user" transition={{ type: "spring", stiffness: 380, damping: 38 }}>
    <div className="shell">
      <aside className="rail"><a href="#" className="brand" onClick={e => { e.preventDefault(); setWorkspace(false); }} aria-label="Deck home"><Command size={24}/></a><button className={!workspace ? "selected" : ""} aria-label="Home" onClick={() => setWorkspace(false)}><span className="home-symbol">⌂</span></button><button aria-label="Open bot chat" onClick={open}><MessageCircle size={20}/></button><span className="rail-spacer"/><span className="avatar">You</span></aside>
      <div className="app">
        <header><button className="wordmark" onClick={() => setWorkspace(false)}>deck<span> / {workspace ? name : "Home"}</span></button><span className="preview-label">Experience preview · sample data</span><button className="icon" aria-label="Reset preview" onClick={() => { act("reset"); setWorkspace(false); setMessages([]); setDraft(""); setOffline(false); }}><RotateCcw size={17}/></button></header>
        <div className="connection" role="status">{offline ? "Reconnecting… Your draft and workspace are kept here." : "A little space. A lot getting done."}</div>
        <AnimatePresence mode="wait" initial={false}>
          {!workspace ? <motion.main key="home" className="home" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
            <div className="greeting"><span className="eyebrow">YOUR SPACE TO GET THINGS DONE</span><h1>Everything in its place.</h1><p>Your bots take care of the work.<br/>You decide what comes next.</p></div>
            <section className="attention-slot" aria-label="Needs you">{needsYou ? <button className="attention" onClick={open}><span className="status-dot amber"/><span><strong>{labels[phase]}</strong><small>{name} · A quick step from you</small></span><ArrowRight size={19}/></button> : <div className="all-clear"><Check size={17}/> Nothing needs your attention. You’re all caught up.</div>}</section>
            <section><div className="section-heading"><h2>Your bots</h2><button className="text-button" onClick={propose}><Plus size={16}/> New bot</button></div><div className="bot-grid"><button className="bot-card" onClick={open}><span className="bot-icon coral"><Mail size={25}/></span><span className="card-arrow"><ArrowUpRight size={19}/></span><h3>{name}</h3><p>A clearer inbox. A calmer morning.</p><span className="bot-status"><span className={`status-dot ${needsYou ? "amber" : ""}`}/>{labels[phase]}</span></button><button className="bot-card new-card" onClick={propose}><span className="plus-circle"><Plus size={24}/></span><h3>Make room for something new.</h3><p>Describe a job. Build your next bot.</p></button></div></section>
            <section className="results"><div className="section-heading"><h2>Recent results</h2><span>Made for you</span></div>{phase === "done" ? <button className="result-row" onClick={open}><span className="result-icon"><Mail size={20}/></span><span><strong>Your morning, in focus</strong><small>Morning mail · Just now · Sample brief</small></span><ArrowUpRight size={19}/></button> : <div className="empty-result">Finished work will land here. Start with your morning mail bot.</div>}</section>
          </motion.main> : <motion.main key="workspace" className="workspace" initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 14 }}>
            <div className="workspace-heading"><button className="icon" aria-label="Back to home" onClick={() => setWorkspace(false)}><ChevronLeft/></button><span className="bot-icon coral"><Mail size={23}/></span><div><h1>{name}</h1><p role="status">{labels[phase]}</p></div>{control}</div>
            <div className="tabs" role="tablist" aria-label="Bot workspace">{[["chat", MessageCircle], ["activity", Activity], ["computer", Monitor]].map(([id, Icon]) => { const value = id as string; const Glyph = Icon as typeof Mail; return <button role="tab" id={`tab-${value}`} aria-selected={tab === value} aria-controls={`panel-${value}`} key={value} onClick={() => setTab(value)}>{tab === value && <motion.span layoutId="tab" className="tab-highlight"/>}<Glyph size={16}/><span>{value}</span></button>; })}</div>
            <div className="workspace-body" role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
              <div hidden={tab !== "chat"} className="conversation">
                <div className="intro"><Sparkles size={21}/><p>A little help with your everyday.<br/><strong>Let’s make mornings easier.</strong></p></div>
                {phase === "idle" ? <div className="journey-card"><h2>Meet your morning mail bot</h2><p>Turn a busy inbox into a short, useful brief. Start by reviewing what it will do.</p><button className="primary" onClick={propose}>Set up this bot <ArrowRight size={16}/></button></div> : <><div className="user-message">{job}</div><div className="journey-card">
                  {phase === "review" && <><span className="eyebrow">YOUR BOT, YOUR CALL</span><h2>A calmer start to your day.</h2><label>Bot name<input value={name} maxLength={60} onChange={e => setName(e.target.value)}/></label><label>Its job<textarea value={job} onChange={e => setJob(e.target.value)}/></label><div className="review-row"><span>Permissions</span><strong>Read and organize mail</strong></div><div className="review-row"><span>Account</span><strong>Connect after review</strong></div><p className="fine-print">Preview only. No account is connected, money spent, or message sent.</p><button className="primary" onClick={() => act("create")}>Create bot <ArrowRight size={16}/></button></>}
                  {phase === "connect" && <><span className="eyebrow">ONE QUICK CONNECTION</span><h2>Bring your inbox along.</h2><p>In the finished app, credentials go through protected input. This preview uses a simulated connection. Don’t enter real credentials.</p><div className="connection-tile"><Mail size={23}/><span>Gmail<small>Your private mail workspace</small></span><span className="badge">Demo</span></div><button className="primary" onClick={() => act("connect")}>Try demo connection <ArrowRight size={16}/></button></>}
                  {phase === "phone" && <><span className="eyebrow">WAITING FOR YOU</span><h2>A quick check on your phone.</h2><p>Approve the sign-in request. You can leave this workspace and come back to the same step.</p><div className="phone-number">42</div><p className="fine-print">Simulated challenge. No phone notification was sent.</p><button className="primary" onClick={() => act("approve")}>Simulate verified approval <Check size={16}/></button></>}
                  {phase === "working" && <><span className="eyebrow">TAKING CARE OF IT</span><h2>Finding what matters.</h2><p>Your bot is preparing a concise morning brief. You can keep chatting or head back home.</p><div className="progress-track"><span/></div><ul className="checklist"><li><Check size={16}/> Account connected</li><li><Check size={16}/> Preferences saved</li><li><span className="status-dot"/> Preparing your brief</li></ul></>}
                  {phase === "paused" && <><h2>Take your time.</h2><p>Your work is paused. Resume when you’re ready.</p>{control}</>}
                  {phase === "failed" && <><span className="eyebrow">LET’S PICK THIS BACK UP</span><h2>The connection was interrupted.</h2><p>Your progress is still here. Try reconnecting to continue preparing the brief.</p><button className="primary" onClick={() => act("retry")}>Reconnect and continue <ArrowRight size={16}/></button></>}
                  {phase === "done" && <><span className="eyebrow">ALL SET · SAMPLE RESULT</span><h2>Your morning, in focus.</h2><p>Three things to know. Everything else can wait.</p><ol className="brief"><li><strong>A decision for today</strong><span>The project proposal is ready for your review.</span></li><li><strong>Your afternoon is clear</strong><span>The planning meeting moved to tomorrow.</span></li><li><strong>Something worth reading</strong><span>Your weekly reading list is ready.</span></li></ol><button className="secondary" aria-expanded={details} onClick={() => setDetails(!details)}>{details ? "Hide" : "View"} work details</button>{details && <p className="fine-print">This is a sample result. No real inbox was read or changed.</p>}</>}
                </div></>}
                {messages.map((message, i) => <div className="user-message" key={i}>{message}</div>)}{messages.length > 0 && <p className="fine-print">Message kept in this preview. Live bot responses will be connected later.</p>}
              </div>
              <div hidden={tab !== "activity"} className="activity-panel"><span className="eyebrow">THE WORK BEHIND THE RESULT</span><h2>One step at a time.</h2><div className="timeline"><p><Check size={16}/> Workspace opened</p><p><Activity size={16}/>{labels[phase]}</p></div><p className="fine-print">This preview shows sample activity, not a live execution log.</p></div>
              <div hidden={tab !== "computer"} className="computer-panel"><Monitor size={38}/><h2>{phase === "phone" || phase === "connect" ? "A little privacy for sign-in." : "A window into the work."}</h2><p>{phase === "phone" || phase === "connect" ? "The live screen stays hidden while sensitive information is entered." : "The bot’s live computer will appear here when connected."}</p><span className="badge">Preview · no live computer</span></div>
            </div>
          </motion.main>}
        </AnimatePresence>
        <footer className="composer-area"><form className="composer" onSubmit={e => { e.preventDefault(); submit(); }}><div className="composer-meta"><span><Sparkles size={14}/>{workspace ? name : "Deck · Direct work across your bots"}</span>{workspace && <select aria-label="Message intent" value={mode} onChange={e => setMode(e.target.value)}><option>Guide this task</option><option>Add a task</option></select>}</div><div className="composer-input"><textarea aria-label={workspace ? `Message ${name}` : "Message Deck"} placeholder={workspace ? "What would you like to do next?" : "What would you like taken care of?"} value={draft} rows={1} onChange={e => setDraft(e.target.value)}/><button className="send" aria-label="Send message" disabled={!draft.trim() || offline}><ArrowUp size={20}/></button></div></form><div className="preview-controls"><span>Try the journey</span><button onClick={() => setOffline(!offline)}>{offline ? "Restore connection" : "Go offline"}</button>{phase === "working" && <><button onClick={() => act("fail")}>Simulate failure</button><button onClick={() => act("finish")}>Finish task</button></>}<span className="local-only">Demo state resets on reload</span></div></footer>
      </div>
    </div>
  </MotionConfig>;
}
createRoot(document.getElementById("root")!).render(<StrictMode><App/></StrictMode>);
