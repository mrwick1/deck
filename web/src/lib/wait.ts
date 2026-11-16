import { ApiError, secretFailure, type SetupRecord, type SetupWait, type TaskSecretRequest } from "./api";

/** NUL, CR and LF cannot go in the guest's NAME=value store. An empty value is no value. Spaces stay. */
export const validSecret = (v: string) => v.length > 0 && v.length <= 4096 && !/[\0\r\n]/.test(v) && new TextEncoder().encode(v).length <= 4096;

export type DeliverResult = { ok: true } | { ok: false; message: string; duplicate?: true };

/**
 * Read the value once, clear its field before the request, clear it again after any outcome.
 * Only fixed status messages reach the UI. Caller errors are never displayed.
 */
export async function deliverSecret(o: {
  read: () => string;
  clear: () => void;
  send: (value: string) => Promise<void>;
  guard?: { busy: boolean };
}): Promise<DeliverResult> {
  if (o.guard?.busy) return { ok: false, message: "Delivery is already in progress.", duplicate: true };
  const value = o.read();
  o.clear();
  if (!validSecret(value)) return { ok: false, message: "The value is empty, contains a line break, or exceeds the supported size." };
  if (o.guard) o.guard.busy = true;
  try {
    await o.send(value);
    return { ok: true };
  } catch (e) {
    o.clear();
    return { ok: false, message: secretFailure(e instanceof ApiError ? e.status : 0) };
  } finally {
    if (o.guard) o.guard.busy = false;
  }
}

export type WaitMode = "secret" | "challenge" | "pending" | "expired" | "blocked";
export interface WaitView { mode: WaitMode; title: string; detail: string; }

export function describeWait(w: SetupWait, protectedInput: boolean, nowSecs: number): WaitView {
  const k = w.kind;
  if (typeof k === "object" && "Unsupported" in k)
    return { mode: "blocked", title: "Blocked: this challenge is not supported", detail: `Deck cannot complete it${k.Unsupported.reason ? ` (${k.Unsupported.reason})` : ""}. Setup stays paused and keeps its progress.` };
  if (typeof k === "string" && w.answered)
    return { mode: "pending", title: "Waiting for verification", detail: "You said this is done. The sign-in is not yet verified." };
  if (w.expires_at != null && w.expires_at <= nowSecs)
    return { mode: "expired", title: "This request expired", detail: "Progress is saved. Refresh to see the replacement request." };
  if (typeof k === "object")
    return protectedInput
      ? { mode: "secret", title: `Protected input needed: ${k.Credential.label}`, detail: "The value goes straight to the bot's computer. It never enters chat or the model." }
      : { mode: "blocked", title: `Blocked: ${k.Credential.label}`, detail: "Protected input is not available on this server. Setup keeps its progress. Do not send credentials in chat." };
  const what = k === "Captcha" ? "Complete the CAPTCHA" : "Approve the sign-in on your phone";
  return { mode: "challenge", title: what, detail: [w.instructions?.trim() || `${what}, then confirm here. Your confirmation does not by itself prove the sign-in worked.`, w.match_number ? `Match number: ${w.match_number}.` : ""].filter(Boolean).join(" ") };
}

/** The pending wait of one bot, by its own id. Never another bot's. */
export const waitFor = (rows: SetupRecord[], botId: string | undefined) =>
  botId === undefined ? undefined : rows.find(r => r.setup.id === botId && r.setup.wait);

/** What an active task asked for. An expired request offers no form: the bot is asked again. */
export function describeTaskSecret(r: TaskSecretRequest, nowSecs: number): WaitView {
  if (r.expires_at <= nowSecs)
    return { mode: "expired", title: `The request for ${r.label} expired`, detail: "The bot is told to ask again if it still needs the value. A new request will appear here. Refresh to check." };
  return { mode: "secret", title: `Protected input needed: ${r.label}`, detail: `${r.purpose} The value goes straight to the bot's computer. It never enters chat or the model.` };
}

/** Run once at a time. A second call while the first runs does nothing and returns false. */
export async function guarded(guard: { busy: boolean }, run: () => Promise<void>): Promise<boolean> {
  if (guard.busy) return false;
  guard.busy = true;
  try { await run(); } finally { guard.busy = false; }
  return true;
}

export type ChallengeOutcome = { rid: string; pending: boolean; text: string; error: boolean } | null;

/** Holds response metadata only. The workspace can retain it while its request tab unmounts. */
export function challengeResponse() {
  let outcome: ChallengeOutcome = null;
  const listeners = new Set<() => void>();
  return {
    guard: { busy: false },
    getSnapshot: () => outcome,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    set: (update: ChallengeOutcome | ((previous: ChallengeOutcome) => ChallengeOutcome)) => {
      outcome = typeof update === "function" ? update(outcome) : update;
      listeners.forEach(listener => listener());
    },
  };
}
