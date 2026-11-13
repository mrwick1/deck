export interface Chat {
  id: number;
  title: string;
  created_at: string;
}

export interface StoredMessage {
  id: number;
  chat_id: number;
  role: string;
  /** A JSON string: an OpenAI chat message or a file message. */
  content: string;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the server");
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      msg = (await res.json()).error ?? msg;
    } catch {
      /* keep the default message */
    }
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const post = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getMe = () => call<{ user: string }>("/api/me");
export const listChats = () => call<Chat[]>("/api/chats");
export const createChat = (title: string) =>
  call<Chat>("/api/chats", post({ title }));
export const listMessages = (id: number) =>
  call<StoredMessage[]>(`/api/chats/${id}/messages`);
export interface SendOptions {
  mode?: "steer" | "queue";
  priority?: number;
}
/** A plain chat answers with the run. The bot chat answers with the task and what happened to the message. */
export const sendMessage = (id: number, text: string, o: SendOptions = {}) =>
  call<{ run_id?: number; task_id?: string; kind?: string; held?: string | null }>(
    `/api/chats/${id}/messages`,
    post({ text, ...o }),
  );
export const cancelRun = (runId: number) =>
  call<void>(`/api/runs/${runId}/cancel`, { method: "POST" });
export const answerApproval = (id: string, approved: boolean) =>
  call<void>(`/api/approvals/${id}`, post({ approved }));
export const eventsUrl = (chatId: number, after: number) =>
  `/api/chats/${chatId}/events?after=${after}`;
export const fileUrl = (id: number) => `/api/files/${id}`;

export interface BotTask {
  id: string;
  title: string;
  priority: number;
  state: "queued" | "running" | "waiting" | "done" | "failed" | "cancelled" | "held";
  detail: string | null;
  source: string;
}
export interface BotScreenStats {
  viewers: number;
  worker: boolean;
  captures: number;
  delivered: number;
  unchanged: number;
}
/** An open request to raise the absolute spending ceiling. Only the owner can answer it. */
export interface CeilingRequest {
  id: string;
  version: number;
  currency: string;
  precision: number;
  current_ceiling_minor: number;
  reason: string;
}
export interface BotStatus {
  bot_id: string;
  chat_id: number;
  computer: "saved" | "waking" | "awake" | "saving" | "failed";
  automation_paused: boolean;
  budget_hold: boolean;
  budget_reason: string | null;
  /** Set by a server with a ledger. Absent from a server without one. */
  ceiling_request?: CeilingRequest | null;
  working: BotTask | null;
  waiting: BotTask | null;
  queued: number;
  tasks: BotTask[];
  unresolved: number;
  idle_secs: number;
  idle_left_ms: number | null;
  pending_events: number;
  last_error: string | null;
  screen: BotScreenStats;
}
export interface InboxItem {
  kind: "budget_hold" | "ceiling_request" | "unknown_outcome" | "waiting_task" | "held_task";
  id: string;
  title: string;
  detail: string;
  task_id: string | null;
  ceiling?: CeilingRequest;
}
export interface CeilingAnswer {
  request_id: string;
  version: number;
  decision: "approve" | "deny";
  amount_minor?: number;
  currency?: string;
  precision?: number;
  idempotency_key: string;
}
export const botStatusUrl = "/api/bot/stream";
export const botScreenUrl = "/api/bot/screen";
export const botFrameUrl = (seq: number) => `/api/bot/screen/latest?seq=${seq}`;
export const getBot = () => call<BotStatus>("/api/bot");
const act = (path: string, body?: unknown) =>
  call<void>(`/api/bot/${path}`, body === undefined ? { method: "POST" } : post(body));
export const botStop = () =>
  call<{ confirmed: string[]; unconfirmed: string[] }>("/api/bot/stop", {
    method: "POST",
  });
export const botPause = (paused: boolean) => act("automation", { paused });
export const botSleep = () => act("sleep");
export const botWake = () => act("wake");
export const botHold = (reason: string) => act("budget-hold", { reason });
/** Releases the old flag hold only. It does not change the spending ceiling. */
export const botApprove = () => act("budget-hold/approve");
export const botAnswerCeiling = (a: CeilingAnswer) =>
  act("ceiling-requests/respond", a);
export const botCancelTask = (id: string) => act(`tasks/${id}/cancel`);
export const botRetryTask = (id: string) => act(`tasks/${id}/retry`);
export const botPriority = (id: string, priority: number) =>
  act(`tasks/${id}/priority`, { priority });
export const getInbox = () => call<InboxItem[]>("/api/bot/inbox");
export const botDecide = (id: string, decision: string, note = "") =>
  act(`inbox/commands/${id}`, { decision, note });
