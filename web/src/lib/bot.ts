import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

export type Tone = "busy" | "attention" | "idle" | "asleep" | "error";

/** One line for the state of the bot: what it does and whether the owner must act. */
export function describeBot(s: api.BotStatus): { label: string; tone: Tone } {
  if (s.computer === "failed")
    return { label: "Computer failed to start or save", tone: "error" };
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

export const PRIORITIES = [
  { value: 0, label: "Low" },
  { value: 1, label: "Normal" },
  { value: 2, label: "High" },
];

/** The bot status, pushed by the server. It is null when the server has no bot. */
export function useBot() {
  const [status, setStatus] = useState<api.BotStatus | null>(null);
  const [inbox, setInbox] = useState<api.InboxItem[]>([]);

  const refreshInbox = useCallback(
    () => api.getInbox().then(setInbox).catch(() => {}),
    [],
  );

  useEffect(() => {
    let es: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    let delay = 500;
    // A 404 means the server runs without a bot. Then nothing else is asked.
    api
      .getBot()
      .then((s) => {
        if (closed) return;
        setStatus(s);
        const open = () => {
          es = new EventSource(api.botStatusUrl);
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
  }, []);

  // The inbox follows the parts of the status that change it.
  const key = status
    ? [status.unresolved, status.budget_hold, status.waiting?.id, status.queued, status.working?.id].join()
    : "";
  useEffect(() => {
    if (key) refreshInbox();
  }, [key, refreshInbox]);

  return { status, inbox, refreshInbox };
}
