import { describe, expect, it } from "vitest";
import { initialFlow, flow, type Flow, type Proposal } from "./flow";
const proposal: Proposal = { name: "Morning mail", job: "Summarize my mail", authority: "restricted", permissions: ["Read mail"], ceiling: "5" };
function ready(): Flow {
  let state = flow(initialFlow, { type: "create", proposal });
  for (const action of ["connect", "approve"] as const) state = flow(state, { type: "act", id: "bot-1", action });
  return state;
}
describe("workspace flow", () => {
  it("keeps each new bot separate and preserves the first bot", () => {
    const first = ready();
    const state = flow(first, { type: "create", proposal: { ...proposal, name: "Second bot" } });
    expect(state.bots.map(b => b.id)).toEqual(["bot-1", "bot-2"]);
    expect(state.bots[0]).toEqual(first.bots[0]);
    expect(state.bots[1].phase).toBe("connect");
  });
  it("keeps home and bot drafts independent across navigation", () => {
    let state = flow(ready(), { type: "draft", scope: "home", text: "Plan my day" });
    state = flow(state, { type: "draft", scope: "bot-1", text: "Read urgent mail first" });
    state = flow(state, { type: "navigate", view: "home" });
    state = flow(state, { type: "navigate", view: "bot-1" });
    expect(state.drafts).toEqual({ home: "Plan my day", "bot-1": "Read urgent mail first" });
  });
  it("queues work without replacing the current task and requires an explicit start", () => {
    let state = flow(ready(), { type: "message", id: "bot-1", text: "Find receipts", mode: "queue" });
    expect(state.bots[0].task).toBe(proposal.job);
    expect(state.bots[0].queue).toEqual(["Find receipts"]);
    state = flow(state, { type: "act", id: "bot-1", action: "finish" });
    expect(state.bots[0].phase).toBe("done");
    state = flow(state, { type: "start-next", id: "bot-1" });
    expect(state.bots[0].task).toBe("Find receipts");
    expect(state.bots[0].phase).toBe("working");
    expect(state.bots[0].queue).toEqual([]);
  });
  it("cannot start queued work through a pause or sign-in wait", () => {
    let state = flow(ready(), { type: "message", id: "bot-1", text: "Find receipts", mode: "queue" });
    state = flow(state, { type: "act", id: "bot-1", action: "pause" });
    expect(flow(state, { type: "start-next", id: "bot-1" })).toEqual(state);
    const waiting = flow(initialFlow, { type: "create", proposal });
    expect(flow(waiting, { type: "start-next", id: "bot-1" })).toEqual(waiting);
  });
  it("rejects incomplete setup and keeps an owner-chosen zero ceiling", () => {
    expect(flow(initialFlow, { type: "create", proposal: { ...proposal, ceiling: "" } })).toEqual(initialFlow);
    expect(flow(initialFlow, { type: "create", proposal: { ...proposal, name: " " } })).toEqual(initialFlow);
    expect(flow(initialFlow, { type: "create", proposal: { ...proposal, ceiling: "0" } }).bots[0].ceiling).toBe("0");
  });
  it("keeps instruction messages out of the queue and ignores empty messages", () => {
    const state = ready();
    expect(flow(state, { type: "message", id: "bot-1", text: " ", mode: "guide" })).toEqual(state);
    const next = flow(state, { type: "message", id: "bot-1", text: "Keep it short", mode: "guide" });
    expect(next.bots[0].queue).toEqual([]);
    expect(next.bots[0].messages[0]).toEqual({ text: "Keep it short", mode: "guide" });
  });
});
