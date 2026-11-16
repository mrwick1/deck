import * as api from "../lib/api";
import { ceilingAnswer, parseMinor } from "../lib/bot";
import { formatMinor } from "./setup-form";

/** One card per kind and id. A ceiling card keeps its identity when the server raises the version. */
export const taskKey = (id: string) => `task:${id}`;
export const itemKey = (item: api.InboxItem) => item.kind === "held_task" || item.kind === "waiting_task" ? taskKey(item.task_id ?? item.id) : `${item.kind}:${item.id}`;

/** Written exactly with integers. Null when the server amount cannot be shown without rounding. */
export function money(minor: number, precision: number, currency: string): string | null {
  try { return `${formatMinor(minor, precision)} ${currency}`; } catch { return null; }
}

export type Amount =
  | { ok: true; minor: number }
  | { ok: false; kind: "empty" | "syntax" | "scale" | "large" | "not-above" | "unsupported"; message: string };

/** Validate the owner's new ceiling. Each failure has its own message, so syntax is never mistaken for "not above". */
export function checkAmount(text: string, r: api.CeilingRequest): Amount {
  const current = money(r.current_ceiling_minor, r.precision, r.currency);
  if (current === null) return { ok: false, kind: "unsupported", message: "Deck cannot show this ceiling exactly, so it cannot be raised here. You can still keep the current ceiling." };
  const value = text.trim();
  if (!value) return { ok: false, kind: "empty", message: `Enter the new ceiling in ${r.currency}.` };
  const m = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!m) return { ok: false, kind: "syntax", message: `Use digits and one decimal point only, for example ${r.precision ? "25.50" : "25"}. Signs, commas and letters are not allowed.` };
  if ((m[2]?.length ?? 0) > r.precision) return { ok: false, kind: "scale", message: r.precision ? `${r.currency} allows at most ${r.precision} decimal places.` : `${r.currency} allows whole numbers only.` };
  const minor = parseMinor(value, r.precision);
  if (minor === null) {
    const exact = BigInt(m[1] + (m[2] ?? "").padEnd(r.precision, "0"));
    if (exact > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, kind: "large", message: "That amount is too large to send exactly." };
  } else if (minor > r.current_ceiling_minor) return { ok: true, minor };
  return { ok: false, kind: "not-above", message: `Enter an amount above the current ceiling of ${current}.` };
}

export type Phase = "pending" | "accepted" | "unknown";
/** One response in flight or not yet proven. It lives in the store, so it survives a tab switch. */
export interface Op { identity: string; label: string; version?: number; phase: Phase; checked: boolean; message: string }
export interface Draft { amount: string; error: string; errorSeq: number; reviewed: number; reviewedMinor: number }
export type Notice = { text: string; tone: "" | "busy" | "ok" | "error" };
export interface View {
  items: api.InboxItem[]; secrets: api.TaskSecretRequest[]; error: string; loaded: boolean;
  ops: ReadonlyMap<string, Op>; drafts: ReadonlyMap<string, Draft>; failures: ReadonlyMap<string, string>;
  review: ReadonlySet<string>; notice: Notice;
}
export type ReadResult = "ok" | "failed" | "stale";
export interface Deps {
  inbox: () => Promise<api.InboxItem[]>;
  secrets: () => Promise<api.TaskSecretRequest[]>;
  answer: (a: api.CeilingAnswer) => Promise<unknown>;
}

const READ_FAILED = "Requests could not be refreshed. Retry before responding.";
/** A rejected answer is final. Status 0 and 5xx can hide an applied write, so they are not final. */
const definitive = (e: unknown) => e instanceof api.ApiError && e.status >= 400 && e.status < 500;

/**
 * State of one bot's requests. The app retains one store per bot,
 * so reads, drafts, idempotency keys and unproven responses survive route changes.
 * The guards are plain fields. They never wait for a React render.
 */
export class RequestsStore {
  private items: api.InboxItem[] = [];
  private secrets: api.TaskSecretRequest[] = [];
  private error = "";
  private loaded = false;
  private notice: Notice = { text: "", tone: "" };
  private ops = new Map<string, Op>();
  private drafts = new Map<string, Draft>();
  private failures = new Map<string, string>();
  private review = new Set<string>();
  /** Identities that left the list after a response. A return of one needs the owner's review. */
  private settled = new Set<string>();
  private keys = new Map<string, string>();
  private gen = 0;
  private inflight: Promise<ReadResult> | null = null;
  private queued: Promise<ReadResult> | null = null;
  private listeners = new Set<() => void>();
  private view: View;

  constructor(private deps: Deps, private newKey: () => string = () => crypto.randomUUID()) { this.view = this.build(); }

  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  getSnapshot = () => this.view;
  private build(): View {
    return {
      items: this.items, secrets: this.secrets, error: this.error, loaded: this.loaded,
      ops: new Map([...this.ops].map(([k, op]) => [k, { ...op }])), drafts: new Map([...this.drafts].map(([k, d]) => [k, { ...d }])),
      failures: new Map(this.failures), review: new Set(this.review), notice: this.notice,
    };
  }
  private emit() { this.view = this.build(); this.listeners.forEach(listener => listener()); }
  private say(text: string, tone: Notice["tone"]) { this.notice = { text, tone }; }

  /**
   * Serial reads. A forced read starts after every earlier read settles, so it cannot return an answer
   * that began before the write. A read that began before a write ended is dropped, never applied.
   */
  read(force = false): Promise<ReadResult> {
    if (!this.inflight) return this.start();
    if (!force) return this.inflight;
    return this.queued ??= this.inflight.then(() => { this.queued = null; return this.inflight ?? this.start(); });
  }
  private start(): Promise<ReadResult> {
    const gen = this.gen;
    let calls: [Promise<api.InboxItem[]>, Promise<api.TaskSecretRequest[]>];
    try { calls = [this.deps.inbox(), this.deps.secrets()]; } catch (e) { calls = [Promise.reject(e), Promise.reject(e)]; }
    const p = (async (): Promise<ReadResult> => {
      try {
        const [inbox, prompts] = await Promise.all(calls);
        if (gen !== this.gen) return "stale";
        this.apply(inbox, prompts); return "ok";
      } catch {
        if (gen !== this.gen) return "stale";
        this.error = READ_FAILED; this.emit(); return "failed";
      } finally { this.inflight = null; }
    })();
    this.inflight = p;
    return p;
  }

  private apply(inbox: api.InboxItem[], prompts: api.TaskSecretRequest[]) {
    const present = new Map(inbox.map(item => [itemKey(item), item]));
    for (const [key, op] of [...this.ops]) {
      if (op.phase === "pending") continue;
      const now = present.get(key);
      if (now && (op.version === undefined || now.ceiling?.version === op.version)) {
        op.checked = true;
        op.message = op.phase === "accepted"
          ? "The server accepted your response but still lists this request. Check status, or review it and respond again."
          : "Not confirmed. The server still lists this request. Review it before you respond again.";
        this.say(`${op.label}: ${op.message}`, "error");
        continue;
      }
      this.ops.delete(key);
      if (!now) { this.settled.add(key); this.drafts.delete(key); }
      this.say(now ? `The request changed after ${op.label}. Review the current request before you respond.` : op.phase === "accepted" ? `${op.label}: confirmed. The request is closed.` : `The server no longer lists this request. ${op.label} may have been applied. Check Activity.`, "ok");
    }
    for (const [key, item] of present) {
      if (this.settled.delete(key)) {
        this.review.add(key);
        this.say("A request returned after a response. Review it before responding again.", "");
      }
      if (item.ceiling && !this.drafts.has(key)) this.drafts.set(key, { amount: "", error: "", errorSeq: 0, reviewed: item.ceiling.version, reviewedMinor: item.ceiling.current_ceiling_minor });
    }
    this.items = inbox; this.secrets = prompts; this.error = ""; this.loaded = true;
    this.emit();
  }

  /** Drops reads that began before now. Called when a write starts and when it ends. */
  private invalidate() { this.gen += 1; }

  /** Send one response. A second call for the same card does nothing while the first is open or unproven. */
  async perform(o: { identity: string; label: string; version?: number }, call: () => Promise<unknown>): Promise<void> {
    if (this.ops.has(o.identity) || this.review.has(o.identity)) return;
    const op: Op = { identity: o.identity, label: o.label, version: o.version, phase: "pending", checked: false, message: `Sending: ${o.label}…` };
    this.ops.set(o.identity, op); this.failures.delete(o.identity);
    this.say(`Sending: ${o.label}…`, "busy"); this.invalidate(); this.emit();
    try {
      await call();
      op.phase = "accepted"; op.message = "The server accepted your response. Checking the current state…";
      this.say(`${o.label}: accepted by the server. Checking the current state…`, "busy");
    } catch (e) {
      if (definitive(e)) {
        this.ops.delete(o.identity);
        const status = (e as api.ApiError).status;
        const text = status === 409 ? `The request changed or needs review: ${(e as Error).message.replace(/\binbox\b/gi, "Requests")}` : status === 404 || status === 410 ? "This request is no longer open. Review the current request." : e instanceof Error ? e.message : "The server rejected the response.";
        this.failures.set(o.identity, text); this.say(`${o.label}: not applied. ${text}`, "error");
        this.invalidate(); this.emit(); await this.reconcile(); return;
      }
      op.phase = "unknown"; op.message = "The server did not confirm your response. It may have been applied. Checking…";
      this.say(`${o.label}: not confirmed. It may have been applied. Checking the current state…`, "error");
    }
    this.invalidate(); this.emit();
    if (await this.reconcile() !== "ok" && this.ops.get(o.identity) === op) {
      op.message = op.phase === "accepted"
        ? "The server accepted your response. The current state could not be read. Use Check status."
        : "Not confirmed, and the current state could not be read. Use Check status. Do not respond again yet.";
      this.say(`${o.label}: the current state could not be read. Use Check status.`, "error"); this.emit();
    }
  }
  /** One forced read after a write. A read dropped for a newer write is asked again once. */
  private async reconcile(): Promise<ReadResult> {
    const first = await this.read(true);
    return first === "stale" ? this.read(true) : first;
  }
  /** The owner's own read of the current state. */
  async check(): Promise<void> { this.say("Checking the current state…", "busy"); this.emit(); if (await this.reconcile() === "ok" && this.notice.tone === "busy") { this.say("", ""); this.emit(); } }

  /** Free the card for a new response, only after the owner chose to review it again. */
  reopen(identity: string) { this.ops.delete(identity); this.failures.delete(identity); this.say("", ""); this.emit(); }
  /** The owner has read a card that came back. */
  confirmReview(identity: string) { this.review.delete(identity); this.emit(); }

  setAmount(identity: string, amount: string) {
    const d = this.drafts.get(identity); if (!d) return;
    d.amount = amount; d.error = ""; this.emit();
  }
  /** Accept the changed request as the one the owner now answers. The typed amount stays. */
  reviewCeiling(item: api.InboxItem) {
    const d = this.drafts.get(itemKey(item)); if (!d || !item.ceiling) return;
    d.reviewed = item.ceiling.version; d.reviewedMinor = item.ceiling.current_ceiling_minor; d.error = ""; this.emit();
  }
  /** Answer a ceiling request. The key is kept for one id, version, decision and exact amount. */
  answerCeiling(item: api.InboxItem, decision: "approve" | "deny"): void {
    const r = item.ceiling, key = itemKey(item), d = this.drafts.get(key);
    if (!r || !d || this.ops.has(key)) return;
    const fail = (message: string) => { d.error = message; d.errorSeq += 1; this.emit(); };
    if (this.review.has(key) || d.reviewed !== r.version) return fail("Review the current request before you respond.");
    let minor = "";
    if (decision === "approve") {
      const amount = checkAmount(d.amount, r);
      if (!amount.ok) return fail(amount.message);
      minor = String(amount.minor);
    }
    const signature = `${r.id}:${r.version}:${decision}:${minor}`;
    let idem = this.keys.get(signature);
    if (!idem) { idem = this.newKey(); this.keys.set(signature, idem); }
    const answer = ceilingAnswer(r, decision, d.amount, idem);
    if (!answer) return fail(`Enter an amount above the current ceiling.`);
    d.error = "";
    void this.perform({ identity: key, label: decision === "approve" ? "Raise ceiling" : "Keep ceiling", version: r.version }, () => this.deps.answer(answer));
  }
}
