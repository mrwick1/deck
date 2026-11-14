import { useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { DropdownMenu } from "radix-ui";
import { ArrowUp, ArrowUpRight, Menu } from "lucide-react";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import "./home-start.css";

const suggestions = [
  ["Morning inbox brief", "Read my inbox each morning and summarize messages that need a reply."],
  ["Research a topic", "Research a topic I choose and prepare a short brief with sources."],
  ["Watch a website", "Check a website I choose and tell me when something important changes."],
];

export function HomeStart({ draft, setDraft, submit, disabled, busy = false, sample = false, notice, actions }: {
  draft: string; setDraft: (value: string) => void; submit: () => void;
  disabled: boolean; busy?: boolean; sample?: boolean; notice?: string;
  actions: { label: string; run: () => void }[];
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const reducedMotion = useReducedMotion();
  const [replaceIdea, setReplaceIdea] = useState<string | null>(null);
  return <div className="home-start">
    <header className="start-header"><span className="start-wordmark">deck</span>{sample && <span className="start-sample">Sample preview</span>}
      <DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Open Home menu" className="start-menu-trigger"><Menu size={20}/></Button></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content className="start-menu" align="end" sideOffset={8} collisionPadding={12}>{actions.map(action => <DropdownMenu.Item key={action.label} className="start-menu-item" onSelect={action.run}>{action.label}</DropdownMenu.Item>)}</DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>
    </header>
    <motion.main className="start-main" initial={reducedMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .18 }}> <h1>What would you like<br/>your bot to do?</h1><p>Give it a job. Review the setup before it starts.</p>
      <section className="start-suggestions" aria-label="Example jobs"><span>Try an idea</span>{suggestions.map(([label, text]) => <Button key={label} variant="outline" className="start-suggestion" disabled={disabled || busy} onClick={() => { if (draft.trim()) setReplaceIdea(text); else { setDraft(text); input.current?.focus(); } }}>{label}<ArrowUpRight size={18}/></Button>)}</section>
      {replaceIdea && <div className="replace-idea" role="group" aria-label="Replace your draft"><p>Replace your current draft with this idea?</p><Button variant="outline" onClick={() => setReplaceIdea(null)}>Keep draft</Button><Button variant="outline" onClick={() => { setDraft(replaceIdea); setReplaceIdea(null); input.current?.focus(); }}>Use idea</Button></div>}
      {sample && <p className="start-note">Sample mail workflow. No accounts connect or work runs here.</p>}
    </motion.main>
    <footer className="start-footer">{notice && <p className="start-notice" role="status">{notice}</p>}
      <form className="start-composer" onSubmit={event => { event.preventDefault(); if (draft.trim() && !disabled && !busy) submit(); }}>
        <label htmlFor="first-job">Your first bot</label><div><Textarea ref={input} id="first-job" rows={1} maxLength={20000} placeholder="Describe its job…" value={draft} onChange={event => setDraft(event.target.value)}/><Button type="submit" size="icon" aria-label={busy ? "Preparing setup" : "Review setup"} className="start-send" disabled={disabled || busy || !draft.trim()}><ArrowUp size={20}/></Button></div>
      </form><p className="start-hint" role="status">{busy ? "Preparing your setup…" : "You’ll approve permissions and spending next."}</p>
    </footer>
  </div>;
}


export function HomeLoading({ error, retry }: { error: string | null; retry: () => void }) {
  return <div className="home-start" aria-busy={!error}>
    <header className="start-header"><span className="start-wordmark">deck</span><Button variant="ghost" size="icon" disabled className="start-menu-trigger" aria-label="Home menu unavailable while connecting"><Menu size={20}/></Button></header>
    <main className="start-main">{error ? <section className="start-unavailable"><h1>Let’s reconnect</h1><p role="alert">{error}</p><Button className="start-suggestion" variant="outline" onClick={retry}>Try again</Button></section> : <><p role="status">Loading your workspace…</p><div className="home-skeleton" aria-hidden="true"><span/><span/><span/></div></>}</main>
    <footer className="start-footer"><div className="start-composer home-composer-skeleton" aria-hidden="true"><span/></div><p className="start-hint">{error ? "Your workspace is unavailable. Nothing was changed." : "Connecting to Deck"}</p></footer>
  </div>;
}
