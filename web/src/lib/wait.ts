import type { SetupRecord, SetupWait } from "./api";

/** NUL, CR and LF cannot go in the guest's NAME=value store. An empty value is no value. Spaces stay. */
export const validSecret = (v: string) => v.length > 0 && !/[\0\r\n]/.test(v);

export type DeliverResult = { ok: true } | { ok: false; message: string };

/**
 * Read the value once, clear its field before the request, clear it again after any outcome.
 * The failure message comes from the caller's error, which never carries the value.
 */
export async function deliverSecret(o: {
  read: () => string;
  clear: () => void;
  send: (value: string) => Promise<void>;
  guard?: { busy: boolean };
}): Promise<DeliverResult> {
  if (o.guard?.busy) return { ok: false, message: "Delivery is already in progress." };
  const value = o.read();
  o.clear();
  if (!validSecret(value)) return { ok: false, message: "Enter a value without line breaks." };
  if (o.guard) o.guard.busy = true;
  try {
    await o.send(value);
    return { ok: true };
  } catch (e) {
    o.clear();
    return { ok: false, message: e instanceof Error ? e.message : "Delivery was not confirmed. Enter the value again." };
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
  if (w.expires_at != null && w.expires_at <= nowSecs)
    return { mode: "expired", title: "This request expired", detail: "Progress is saved. Refresh to see the replacement request." };
  if (typeof k === "object")
    return protectedInput
      ? { mode: "secret", title: `Protected input needed: ${k.Credential.label}`, detail: "The value goes straight to the bot's computer. It never enters chat or the model." }
      : { mode: "blocked", title: `Blocked: ${k.Credential.label}`, detail: "Protected input is not available on this server. Setup keeps its progress. Do not send credentials in chat." };
  const what = k === "Captcha" ? "Complete the CAPTCHA" : "Approve the sign-in on your phone";
  if (w.verification_pending)
    return { mode: "pending", title: "Waiting for verification", detail: "You said this is done. The sign-in is not yet verified, so setup has not resumed." };
  return { mode: "challenge", title: what, detail: w.instruction?.trim() || `${what}, then confirm here. Your confirmation does not by itself prove the sign-in worked.` };
}

/** The pending wait of one bot, by its own id. Never another bot's. */
export const waitFor = (rows: SetupRecord[], botId: string | undefined) =>
  botId === undefined ? undefined : rows.find(r => r.setup.id === botId && r.setup.wait);
