import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

export type Tone = "busy" | "attention" | "idle" | "asleep" | "error";

/** One line for the state of the bot: what it does and whether the owner must act. */
export function describeBot(s: api.BotStatus): { label: string; tone: Tone } {
  if (s.computer === "failed")
    return { label: "Computer failed to start or save", tone: "error" };
  if (s.ceiling_request)
    return { label: "Spending ceiling needs your decision", tone: "attention" };
  if (s.budget_hold) return { label: "Budget hold", tone: "attention" };
  if (s.unresolved > 0)
    return { label: "An outcome needs your decision", tone: "attention" };
  if (s.waiting) return { label: "Waiting for your answer", tone: "attention" };
  if (s.working) return { label: "Working", tone: "busy" };
  if (s.computer === "waking") return { label: "Waking the computer", tone: "busy" };
  if (s.computer === "saving") return { label: "Saving the computer", tone: "busy" };
  if (s.automation_paused) return { label: "Automation paused", tone: "attention" };
  if (s.queued > 0) return { label: `${s.queued} queued`, tone: "busy" };
  if (s.computer === "saved") return { label: "Asleep", tone: "asleep" };
  return { label: "Awake, idle", tone: "idle" };
}

/**
 * Read a decimal amount such as "12.5" as whole minor units, with integer arithmetic only.
 * Returns null for anything else: a sign, an exponent, too many decimals or an unsafe size.
 */
export function parseMinor(text: string, precision: number): number | null {
  const m = /^(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!m || (m[2]?.length ?? 0) > precision) return null;
  const n = BigInt(m[1] + (m[2] ?? "").padEnd(precision, "0"));
  return n > 0n && n <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(n) : null;
}

/** Build the owner's answer to a ceiling request. Null when an approval has no valid amount. */
export function ceilingAnswer(
  r: api.CeilingRequest,
  decision: "approve" | "deny",
  amountText: string,
  key: string,
): api.CeilingAnswer | null {
  const base = { request_id: r.id, version: r.version, decision, idempotency_key: key };
  if (decision === "deny") return base;
  const amount = parseMinor(amountText, r.precision);
  if (amount === null || amount <= r.current_ceiling_minor) return null;
  return { ...base, amount_minor: amount, currency: r.currency, precision: r.precision };
}

export const PRIORITIES = [
  { value: 0, label: "Low" },
  { value: 1, label: "Normal" },
  { value: 2, label: "High" },
];

/** The bot status, pushed by the server. It is null when the server has no bot. */
export function useBot(id?: string) {
  const [loadedId,setLoadedId] = useState<string | undefined>(id);
  const [status, setStatus] = useState<api.BotStatus | null>(null);
  const [inbox, setInbox] = useState<api.InboxItem[]>([]);

  const refreshInbox = useCallback(
    () => api.getInbox(id).then(setInbox).catch(() => {}),
    [id],
  );

  useEffect(() => {
    setStatus(null);
    setInbox([]);
    let es: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let delay = 500;
    // A 404 means the server runs without a bot. Then nothing else is asked.
    api
      .getBot(id)
      .then((s) => {
        if (closed) return;
        setLoadedId(id);
        setStatus(s);
        const open = () => {
          es = new EventSource(api.botStatusUrl(id));
          es.addEventListener("status", (e) => {
            delay = 500;
            setStatus(JSON.parse((e as MessageEvent).data));
          });
          es.onerror = () => {
            es?.close();
            if (closed) return;
            timer = setTimeout(open, delay);
            delay = Math.min(delay * 2, 10_000);
          };
        };
        open();
      })
      .catch(() => {});
    return () => {
      closed = true;
      clearTimeout(timer);
      es?.close();
    };
  }, [id]);

  // The inbox follows the parts of the status that change it.
  const key = status
    ? [status.bot_id, status.unresolved, status.budget_hold, status.ceiling_request?.version, status.waiting?.id, status.queued, status.working?.id].join()
    : "";
  useEffect(() => {
    if (key) refreshInbox();
  }, [key, refreshInbox]);

  return { status: loadedId === id ? status : null, inbox: loadedId === id ? inbox : [], refreshInbox };
}
