import { describe, expect, it } from "vitest";
import type { BotStatus } from "./api";
import { describeBot } from "./bot";

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
