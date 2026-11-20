import { useEffect, useState } from "react";
import type { BotStatus } from "../lib/api";
import { gmailHealth } from "../lib/gmail";
import "./gmail.css";

/** Real Gmail account, summary and label state. `stale` marks a failed refresh: the data may be old. */
export function GmailHealth({ status, stale }: { status: BotStatus; stale: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(timer); }, []);
  if (!status.gmail) return null;
  const health = gmailHealth(status.gmail, now);
  const level = stale ? "stale" : health.level;
  return <section className="gmail-health" aria-label="Gmail status" data-level={level}>
    <h2>Gmail</h2>
    <p className="gmail-headline" role="status">{stale ? "Showing the last status. Deck cannot be reached." : health.headline}</p>
    <ul className="gmail-accounts">{health.accounts.map(account => <li key={account.key} data-level={stale && account.level === "ok" ? "stale" : account.level}><strong>{account.key}</strong><span>{account.text}</span></li>)}</ul>
    {health.labels.length > 0 && <p className="fine-print">{health.labels.join(" · ")}</p>}
    {health.summary.length > 0 && <p className="fine-print">{health.summary.join(" · ")}</p>}
    <p className="fine-print">This bot reads mail and posts the morning summary. It takes no tasks from chat.</p>
  </section>;
}
