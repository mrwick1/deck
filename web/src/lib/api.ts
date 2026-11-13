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
export const botBase = (id?: string) => id ? `/api/bots/${encodeURIComponent(id)}` : "/api/bot";
export const botStatusUrl = (id?: string) => `${botBase(id)}/stream`;
export const botScreenUrl = (id?: string) => `${botBase(id)}/screen`;
export const botFrameUrl = (seq: number, id?: string) => `${botBase(id)}/screen/latest?seq=${seq}`;
export const getBot = (id?: string) => call<BotStatus>(botBase(id));
const act = (path: string, body?: unknown, id?: string) => call<void>(`${botBase(id)}/${path}`, body === undefined ? { method: "POST" } : post(body));
export const botStop = (id?: string) => call<{ confirmed: string[]; unconfirmed: string[] }>(`${botBase(id)}/stop`, {method:"POST"});
export const botPause = (paused: boolean, id?: string) => act("automation", { paused }, id);
export const botSleep = (id?: string) => act("sleep", undefined, id);
export const botWake = (id?: string) => act("wake", undefined, id);
export const botHold = (reason: string, id?: string) => act("budget-hold", {reason}, id);
export const botApprove = (id?: string) => act("budget-hold/approve", undefined, id);
export const botAnswerCeiling = (a: CeilingAnswer, id?: string) => act("ceiling-requests/respond", a, id);
export const botCancelTask = (task: string, id?: string) => act(`tasks/${task}/cancel`, undefined, id);
export const botRetryTask = (task: string, id?: string) => act(`tasks/${task}/retry`, undefined, id);
export const botPriority = (task: string, priority: number, id?: string) => act(`tasks/${task}/priority`, {priority}, id);
export const getInbox = (id?: string) => call<InboxItem[]>(`${botBase(id)}/inbox`);
export const botDecide = (task: string, decision: string, note = "", id?: string) => act(`inbox/commands/${task}`, {decision,note}, id);

export interface SetupBudget { currency:string; exponent:number; period:string; allowance:number; ceiling:number; }
export interface SetupDraft {
  revision:number; name:string; instructions:string; model:string;
  authority:"Full" | {Restricted:string[]}; resources:{cpus:number; memory_mib:number; disk_gib:number};
  budget:SetupBudget | null; credentials:string[]; task:string | null;
}
/** A durable owner wait. Optional fields default to absent on older servers. */
export type WaitKind = "PhoneApproval" | "Captcha" | {Credential:{label:string}} | {Unsupported:{reason?:string | null}};
export interface SetupWait {
  request_id:string; kind:WaitKind;
  /** Unix seconds. Null or absent never expires. */
  expires_at?:number | null;
  /** Service-specific owner instructions, such as a number to match. */
  instructions?:string | null;
  match_number?:string | null;
  /** The owner said done. The server has not yet verified the service. */
  answered?:boolean;
}
export interface SetupRecord {
  version:number; chat_id:number; phase:"Draft" | "Provisioning" | "Waiting" | "Investigate" | "Exhausted" | "Ready";
  runtime_ready:boolean; error:string | null; checklist:{label:string;done:boolean}[];
  setup:{id:string;draft:SetupDraft;max_attempts:number;wait:SetupWait | null;steps:{step:unknown;status:string;last_error:string | null}[]};
}
export const setupOptions = () => call<{enabled:boolean;model?:string;protected_input?:boolean}>("/api/setup-options");
export const listSetups = () => call<SetupRecord[]>("/api/setups");
export const createSetup = (job:string) => call<SetupRecord>("/api/setups",post({job}));
export const editSetup = (id:string,version:number,draft:SetupDraft) => call<void>(`/api/setups/${id}`,post({version,draft}));
export const approveSetup = (id:string,version:number,revision:number) => call<void>(`/api/setups/${id}/approve`,post({version,revision}));
export const controlSetup = (id:string,version:number,command:unknown) => call<void>(`/api/setups/${id}/control`,post({version,command}));

/** Protected input. The value goes only in this body. It never goes in a URL, a message or an error. */
export const submitSecret = async (id:string,version:number,request_id:string,value:string):Promise<void> => {
  let res: Response;
  try { res = await fetch(`/api/setups/${encodeURIComponent(id)}/secret`, post({version,request_id,value})); }
  catch { throw new ApiError(0,"Cannot reach the server. Enter the value again."); }
  // Only 204 proves verified delivery. The server text is never shown, because it could echo input.
  if (res.status !== 204) throw new ApiError(res.status, secretFailure(res.status));
};
export const secretFailure = (status:number) =>
  status === 409 || status === 410 ? "This request changed or expired. Review the current request and enter the value again."
  : status === 401 || status === 403 ? "Sign in again, then enter the value again."
  : status === 501 || status === 503 ? "Protected input is not available. Nothing was delivered."
  : "Delivery was not confirmed. Enter the value again.";
export type ChallengeDecision = "completed" | "cancel";
export const respondChallenge = (id:string,version:number,request_id:string,decision:ChallengeDecision) =>
  call<void>(`/api/setups/${encodeURIComponent(id)}/challenge/respond`,post({version,request_id,decision}));

/** An open protected-input request of the bot's active task. Metadata only. Unix seconds. */
export interface TaskSecretRequest { request_id:string; label:string; purpose:string; expires_at:number; }
export const listSecretRequests = (botId:string) => call<TaskSecretRequest[]>(`${botBase(botId)}/secret-requests`);
/** Active-task protected input. Not a setup answer: it has no version. The value goes only in this body. */
export const submitTaskSecret = async (botId:string,request_id:string,value:string):Promise<void> => {
  let res: Response;
  try { res = await fetch(`${botBase(botId)}/secret`, post({request_id,value})); }
  catch { throw new ApiError(0,"Cannot reach the server. Enter the value again."); }
  if (res.status !== 204) throw new ApiError(res.status, secretFailure(res.status));
};
