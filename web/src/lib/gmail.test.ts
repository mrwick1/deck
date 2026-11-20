import { describe, expect, it } from "vitest";
import type { GmailStatus } from "./api";
import { gmailHealth, isGmailBot } from "./gmail";

const NOW = 1_800_000_000_000;
const base = (over: Partial<GmailStatus> = {}): GmailStatus => ({
  mode: "triage", running: false, stage: null, interval_secs: 120, next_due_in_ms: 100000, overdue_ms: 0, unprocessed_events: 0,
  accounts: [{ key: "account_a", checked: true, result: "ok", last_attempt_ms: NOW - 60000, last_success_ms: NOW - 60000 }],
  summary: { time: "08:00", timezone: "Asia/Kolkata", labels: "configured", label_halted: false, label_states: { applied: 6, uncertain: 1 }, next_summary: "2026-11-16 08:00", last_delivery: { age_ms: 1, day: "2026-11-15" }, triage_backlog: { account_a: [6, 0] }, triage_errors: {} },
  ...over,
});

describe("gmailHealth", () => {
  it("reports a fresh ok account as ok and shows summary and label state", () => {
    const h = gmailHealth(base(), NOW);
    expect(h.level).toBe("ok");
    expect(h.accounts[0]).toEqual({ key: "Account A", level: "ok", text: "Checked just now" });
    expect(h.labels).toContain("6 applied, 1 uncertain");
    expect(h.summary).toContain("Last summary: 2026-11-15");
    expect(h.summary).toContain("Next summary: 2026-11-16 08:00 IST");
  });
  it("marks an old success stale, distinct from failed", () => {
    const g = base({ accounts: [{ key: "account_b", checked: true, result: "ok", last_attempt_ms: NOW, last_success_ms: NOW - 8_000_000 }] });
    const h = gmailHealth(g, NOW);
    expect(h.level).toBe("stale");
    expect(h.accounts[0].text).toBe("Last success 2 h ago");
  });
  it("marks a failed last result as failed and names the count", () => {
    const g = base({ accounts: [{ key: "account_a", checked: true, result: "scan_error", last_attempt_ms: NOW, last_success_ms: NOW - 1000 }, { key: "account_b", checked: true, result: "ok", last_attempt_ms: NOW, last_success_ms: NOW }] });
    const h = gmailHealth(g, NOW);
    expect(h.level).toBe("failed");
    expect(h.headline).toBe("1 of 2 accounts failed their last check");
  });
  it("treats an unchecked account as stale and an overdue poller as stale", () => {
    expect(gmailHealth(base({ accounts: [{ key: "account_a", checked: false, result: null, last_attempt_ms: 0, last_success_ms: 0 }] }), NOW).level).toBe("stale");
    expect(gmailHealth(base({ overdue_ms: 10 * 60000 }), NOW).level).toBe("stale");
  });
  it("shows halted labels and triage errors", () => {
    const g = base(); g.summary!.label_halted = true; g.summary!.triage_errors = { account_a: 1 };
    const h = gmailHealth(g, NOW);
    expect(h.labels).toEqual(expect.arrayContaining(["Label writes halted", "1 triage errors"]));
  });
  it("only a bot with gmail status is a Gmail bot", () => {
    expect(isGmailBot({ gmail: base() })).toBe(true);
    expect(isGmailBot({})).toBe(false);
    expect(isGmailBot({ gmail: null })).toBe(false);
  });
});
