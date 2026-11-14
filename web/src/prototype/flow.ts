import { journey, type Action, type Phase } from "./journey";
export type Proposal = { name: string; job: string; authority: "full" | "restricted"; permissions: string[]; ceiling: string };
export type Bot = Proposal & { id: string; phase: Phase; task: string; queue: string[]; messages: { text: string; mode: "guide" | "queue" }[]; history: string[]; results: string[] };
export type Flow = { view: string; bots: Bot[]; drafts: Record<string, string> };
export const initialFlow: Flow = { view: "home", bots: [], drafts: {} };
export type Event =
  | { type: "navigate"; view: string }
  | { type: "draft"; scope: string; text: string }
  | { type: "create"; proposal: Proposal }
  | { type: "act"; id: string; action: Action }
  | { type: "message"; id: string; text: string; mode: "guide" | "queue" }
  | { type: "start-next"; id: string }
  | { type: "reset" };
export function validProposal(p: Proposal) {
  return !!p.name.trim() && !!p.job.trim() && /^\d+(\.\d{1,2})?$/.test(p.ceiling) && Number.isFinite(Number(p.ceiling));
}
export function flow(state: Flow, event: Event): Flow {
  if (event.type === "reset") return initialFlow;
  if (event.type === "navigate") return { ...state, view: event.view };
  if (event.type === "draft") return { ...state, drafts: { ...state.drafts, [event.scope]: event.text } };
  if (event.type === "create") {
    if (!validProposal(event.proposal)) return state;
    const id = `bot-${state.bots.length + 1}`;
    const bot: Bot = { ...event.proposal, name: event.proposal.name.trim(), job: event.proposal.job.trim(), id, phase: "connect", task: event.proposal.job.trim(), queue: [], messages: [], history: ["Setup approved", "Waiting for account connection"], results: [] };
    return { ...state, view: id, bots: [...state.bots, bot] };
  }
  return { ...state, bots: state.bots.map(bot => {
    if (bot.id !== event.id) return bot;
    if (event.type === "message") {
      const text = event.text.trim();
      if (!text) return bot;
      return { ...bot, queue: event.mode === "queue" ? [...bot.queue, text] : bot.queue, messages: [...bot.messages, { text, mode: event.mode }], history: [...bot.history, event.mode === "queue" ? `Task queued: ${text}` : `Instruction added: ${text}`] };
    }
    if (event.type === "start-next") {
      if (bot.phase !== "done" || !bot.queue.length) return bot;
      return { ...bot, phase: "working", task: bot.queue[0], queue: bot.queue.slice(1), history: [...bot.history, `Started: ${bot.queue[0]}`] };
    }
    const phase = journey(bot.phase, event.action);
    if (phase === bot.phase) return bot;
    const notes: Partial<Record<Action, string>> = { connect: "Demo account connected; waiting for approval", approve: "Demo sign-in verified; task started", pause: "Work paused by you", resume: "Work resumed", fail: "Demo connection interrupted; progress kept", retry: "Demo connection restored; work resumed", finish: "Sample result ready" };
    return { ...bot, phase, history: [...bot.history, notes[event.action] ?? phase], results: phase === "done" ? [...bot.results, bot.task] : bot.results };
  }) };
}
