import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
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

export function HomeStart({ draft, setDraft, submit, disabled, busy = false, sample = false, notice, noticeIsError = false, retry, retryBusy = false, recovery, actions }: {
  draft: string; setDraft: (value: string) => void; submit: () => void;
  disabled: boolean; busy?: boolean; sample?: boolean; notice?: string; noticeIsError?: boolean; retry?: () => void; retryBusy?: boolean; recovery?: ReactNode;
  actions: { label: string; run: () => void }[];
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const reducedMotion = useReducedMotion();
  useLayoutEffect(() => {
    const element = input.current;
    if (!element || CSS.supports("field-sizing", "content")) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(136, Math.max(44, element.scrollHeight))}px`;
  }, [draft]);
  const [replaceIdea, setReplaceIdea] = useState<string | null>(null);
  const replacement = useRef<HTMLDivElement>(null);
  const keepDraft = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (!replaceIdea) return;
    keepDraft.current?.focus({ preventScroll: true });
    replacement.current?.scrollIntoView({ block: "nearest", behavior: "instant" });
  }, [replaceIdea]);
  useEffect(() => { if (noticeIsError) input.current?.focus(); }, [notice, noticeIsError]);
  const useIdea = (text: string) => {
    setDraft(text);
    input.current?.focus();
    requestAnimationFrame(() => input.current?.setSelectionRange(text.length, text.length));
  };
  return <div className="home-start">
    <header className="start-header"><span className="start-wordmark">deck</span>{sample && <span className="start-sample">Sample preview</span>}
      <DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Open Home menu" className="start-menu-trigger"><Menu size={20}/></Button></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content className="start-menu" align="end" sideOffset={8} collisionPadding={12}>{actions.map(action => <DropdownMenu.Item key={action.label} className="start-menu-item" onSelect={action.run}>{action.label}</DropdownMenu.Item>)}</DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>
    </header>
    <motion.main className="start-main" initial={reducedMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: .18 }}> <h1>What would you like your bot to do?</h1><p>Give it a job. Review the setup before it starts.</p>
      <section className="start-suggestions" aria-label="Example jobs"><span>Try an idea</span>{suggestions.map(([label, text]) => <Button key={label} variant="outline" className="start-suggestion" disabled={disabled || busy} onClick={() => { if (draft.trim()) setReplaceIdea(text); else { useIdea(text); } }}>{label}<ArrowUpRight size={18}/></Button>)}</section>
      {replaceIdea && <div ref={replacement} className="replace-idea" role="group" aria-label="Replace your draft"><p>Replace your current draft with this idea?</p><Button ref={keepDraft} variant="outline" onClick={() => { setReplaceIdea(null); input.current?.focus(); }}>Keep draft</Button><Button variant="outline" onClick={() => { useIdea(replaceIdea); setReplaceIdea(null); }}>Use idea</Button></div>}
      {recovery}
      {sample && <p className="start-note">Sample mail workflow. No accounts connect or work runs here.</p>}
    </motion.main>
    <footer className="start-footer">
      <form className="start-composer" onSubmit={event => { event.preventDefault(); if (draft.trim() && !disabled && !busy) submit(); }}>
        <label htmlFor="first-job">Your first bot</label><div><Textarea ref={input} id="first-job" rows={1} maxLength={20000} placeholder="Describe its job…" value={draft} aria-busy={busy} onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} onChange={event => setDraft(event.target.value)}/><Button type="submit" size="icon" aria-label={busy ? "Preparing setup" : "Review setup"} className="start-send" disabled={disabled || busy || !draft.trim()}><ArrowUp size={20}/></Button></div>
      </form><div className="start-feedback"><span role={noticeIsError ? "alert" : "status"}>{busy ? "Preparing your setup…" : notice || "You’ll approve permissions and spending next."}</span>{notice && retry && <Button type="button" variant="ghost" disabled={retryBusy} onClick={retry}>Refresh</Button>}</div>
    </footer>
  </div>;
}


export function HomeLoading({ error, retry }: { error: string | null; retry: () => void }) {
  return <div className="home-start" aria-busy={!error}>
    <header className="start-header"><span className="start-wordmark">deck</span><Button variant="ghost" size="icon" disabled className="start-menu-trigger" aria-label="Home menu unavailable while connecting"><Menu size={20}/></Button></header>
    <main className="start-main">{error ? <section className="start-unavailable"><h1>Let’s reconnect</h1><p role="alert">{error}</p><Button className="start-suggestion" variant="outline" onClick={retry}>Try again</Button></section> : <><p role="status">Loading your workspace…</p><div className="home-skeleton" aria-hidden="true"><span/><span/><span/></div></>}</main>
    <footer className="start-footer"><div className="start-composer home-composer-skeleton" aria-hidden="true"><span/></div><div className="start-feedback"><span>{error ? "Your workspace is unavailable. Nothing was changed." : "Connecting to Deck"}</span></div></footer>
  </div>;
}
