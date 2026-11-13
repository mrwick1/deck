import { describe, expect, it } from "vitest";
import type { BotStatus } from "./api";
import type { CeilingRequest } from "./api";
import { ceilingAnswer, describeBot, parseMinor } from "./bot";

const base: BotStatus = {
  bot_id: "main",
  chat_id: 1,
  computer: "awake",
  automation_paused: false,
  budget_hold: false,
  budget_reason: null,
  working: null,
  waiting: null,
  queued: 0,
  tasks: [],
  unresolved: 0,
  idle_secs: 120,
  idle_left_ms: null,
  pending_events: 0,
  last_error: null,
  screen: { viewers: 0, worker: false, captures: 0, delivered: 0, unchanged: 0 },
};
const task = { id: "t", title: "x", priority: 1, state: "running", detail: null, source: "message" } as const;

describe("describeBot", () => {
  it("names the idle and sleeping states", () => {
    expect(describeBot(base).label).toBe("Awake, idle");
    expect(describeBot({ ...base, computer: "saved" }).label).toBe("Asleep");
  });
  it("asks for the owner before it shows work", () => {
    const s = { ...base, working: task, unresolved: 1 };
    expect(describeBot(s)).toEqual({
      label: "An outcome needs your decision",
      tone: "attention",
    });
    expect(describeBot({ ...base, budget_hold: true, working: task }).label).toBe("Budget hold");
  });
  it("tells a pause from a sleep", () => {
    const s = { ...base, computer: "saved" as const, automation_paused: true };
    expect(describeBot(s).label).toBe("Automation paused");
  });
  it("shows a failed computer first", () => {
    expect(describeBot({ ...base, computer: "failed", working: task }).tone).toBe("error");
  });
});

const req: CeilingRequest = {
  id: "r1",
  version: 2,
  currency: "USD",
  precision: 2,
  current_ceiling_minor: 2000,
  reason: "next call does not fit",
};

describe("ceiling request", () => {
  it("is not the same state as a plain budget hold", () => {
    const s = { ...base, budget_hold: true, ceiling_request: req };
    expect(describeBot(s).label).toBe("Spending ceiling needs your decision");
    expect(describeBot({ ...base, budget_hold: true }).label).toBe("Budget hold");
  });
  it("reads amounts with integers only", () => {
    expect(parseMinor("50", 2)).toBe(5000);
    expect(parseMinor("12.5", 2)).toBe(1250);
    expect(parseMinor("0.07", 2)).toBe(7);
    for (const bad of ["", "-1", "1e3", "1.234", "0", "0.00", "abc", "99999999999999999999"])
      expect(parseMinor(bad, 2), bad).toBeNull();
  });
  it("answers with the version, currency and precision of the request", () => {
    expect(ceilingAnswer(req, "approve", "50", "k")).toEqual({
      request_id: "r1",
      version: 2,
      decision: "approve",
      amount_minor: 5000,
      currency: "USD",
      precision: 2,
      idempotency_key: "k",
    });
    expect(ceilingAnswer(req, "deny", "", "k")).toEqual({
      request_id: "r1",
      version: 2,
      decision: "deny",
      idempotency_key: "k",
    });
  });
  it("refuses an approval that does not raise the ceiling", () => {
    expect(ceilingAnswer(req, "approve", "20", "k")).toBeNull();
    expect(ceilingAnswer(req, "approve", "", "k")).toBeNull();
  });
});
