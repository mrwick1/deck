import { useRef } from "react";
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
  return <div className="home-start">
    <header className="start-header"><span className="start-wordmark">deck</span>{sample && <span className="start-sample">Sample preview</span>}
      <DropdownMenu.Root><DropdownMenu.Trigger asChild><Button variant="ghost" size="icon" aria-label="Open Home menu" className="start-menu-trigger"><Menu size={20}/></Button></DropdownMenu.Trigger>
        <DropdownMenu.Portal><DropdownMenu.Content className="start-menu" align="end" sideOffset={8} collisionPadding={12}>{actions.map(action => <DropdownMenu.Item key={action.label} className="start-menu-item" onSelect={action.run}>{action.label}</DropdownMenu.Item>)}</DropdownMenu.Content></DropdownMenu.Portal>
      </DropdownMenu.Root>
    </header>
    <main className="start-main"><h1>What would you like<br/>your bot to do?</h1><p>Give it a job. Review the setup before it starts.</p>
      <section className="start-suggestions" aria-label="Example jobs"><span>Try an idea</span>{suggestions.map(([label, text]) => <Button key={label} variant="outline" className="start-suggestion" disabled={disabled || busy} onClick={() => { setDraft(text); input.current?.focus(); }}>{label}<ArrowUpRight size={18}/></Button>)}</section>
      {sample && <p className="start-note">Sample mail workflow. No accounts connect or work runs here.</p>}
    </main>
    <footer className="start-footer">{notice && <p className="start-notice" role="status">{notice}</p>}
      <form className="start-composer" onSubmit={event => { event.preventDefault(); if (draft.trim() && !disabled && !busy) submit(); }}>
        <label htmlFor="first-job">Your first bot</label><div><Textarea ref={input} id="first-job" rows={1} maxLength={20000} placeholder="Describe its job…" value={draft} onChange={event => setDraft(event.target.value)}/><Button type="submit" size="icon" aria-label={busy ? "Preparing setup" : "Review setup"} className="start-send" disabled={disabled || busy || !draft.trim()}><ArrowUp size={20}/></Button></div>
      </form><p className="start-hint">{busy ? "Preparing your setup…" : "You’ll approve permissions and spending next."}</p>
    </footer>
  </div>;
}
