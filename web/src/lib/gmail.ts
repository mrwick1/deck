import type { BotStatus, GmailStatus } from "./api";

export type Level = "ok" | "stale" | "failed";
export interface AccountHealth { key: string; level: Level; text: string }
export interface GmailHealth {
  level: Level;
  headline: string;
  accounts: AccountHealth[];
  labels: string[];
  summary: string[];
}

const mins = (ms: number) => Math.max(0, Math.round(ms / 60000));
const ago = (ms: number) => (ms < 90000 ? "just now" : mins(ms) < 120 ? `${mins(ms)} min ago` : `${Math.round(ms / 3600000)} h ago`);
const label = (key: string) => `Account ${key.replace(/^account_/, "").toUpperCase()}`;

/** A bot that polls Gmail has no composer: a chat message must never start a mailbox task. */
export const isGmailBot = (s: Pick<BotStatus, "gmail">) => !!s.gmail;

/**
 * Real account health from the server's own counters. An account is stale when its last success is
 * older than three poll intervals, and failed when its last result is not "ok". Nothing is invented.
 */
export function gmailHealth(g: GmailStatus, now: number): GmailHealth {
  const limit = Math.max(g.interval_secs, 60) * 3000;
  const accounts = g.accounts.map<AccountHealth>(a => {
    if (!a.checked) return { key: label(a.key), level: "stale", text: "Not checked yet" };
    if (a.result !== "ok") return { key: label(a.key), level: "failed", text: `Last check failed${a.result ? `: ${a.result}` : ""}` };
    const age = now - a.last_success_ms;
    return age > limit ? { key: label(a.key), level: "stale", text: `Last success ${ago(age)}` } : { key: label(a.key), level: "ok", text: `Checked ${ago(age)}` };
  });
  const failed = accounts.filter(a => a.level === "failed").length, stale = accounts.filter(a => a.level === "stale").length;
  const level: Level = failed ? "failed" : stale || g.overdue_ms > limit ? "stale" : "ok";
  const headline = failed ? `${failed} of ${accounts.length} accounts failed their last check`
    : level === "stale" ? "Mail checks are late" : g.running ? "Checking mail now" : "All accounts are being checked";
  const s = g.summary;
  const labels: string[] = [];
  const summary: string[] = [];
  if (s) {
    const states = Object.entries(s.label_states).map(([k, v]) => `${v} ${k}`);
    labels.push(s.labels === "configured" ? "Labels on" : "Labels off");
    if (states.length) labels.push(states.join(", "));
    if (s.label_halted) labels.push("Label writes halted");
    const backlog = Object.values(s.triage_backlog).reduce((n, v) => n + (v[0] ?? 0), 0);
    if (backlog) labels.push(`${backlog} waiting to be triaged`);
    const errors = Object.keys(s.triage_errors).length;
    if (errors) labels.push(`${errors} triage errors`);
    if (s.last_delivery) summary.push(`Last summary: ${s.last_delivery.day}`);
    if (s.next_summary) summary.push(`Next summary: ${s.next_summary} ${s.timezone === "Asia/Kolkata" ? "IST" : s.timezone}`);
  }
  if (g.unprocessed_events) summary.push(`${g.unprocessed_events} events not yet processed`);
  return { level, headline, accounts, labels, summary };
}
