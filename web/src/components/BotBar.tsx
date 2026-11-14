import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import { PRIORITIES, ceilingAnswer, describeBot, type Tone } from "@/lib/bot";
import { cn } from "@/lib/utils";
import {
  BellIcon,
  MonitorIcon,
  MoonIcon,
  PauseIcon,
  PlayIcon,
  SquareIcon,
  SunIcon,
} from "lucide-react";
import { createContext, useContext, useEffect, useState } from "react";
import { toast } from "sonner";

export const BotScope = createContext<string | undefined>(undefined);

const fail = (e: unknown) => toast.error((e as Error).message);

const TONE: Record<Tone, string> = {
  busy: "bg-emerald-500",
  attention: "bg-amber-500",
  idle: "bg-sky-500",
  asleep: "bg-muted-foreground/40",
  error: "bg-red-500",
};

export function StatusDot({ tone }: { tone: Tone }) {
  return <span className={cn("inline-block size-2 rounded-full", TONE[tone])} aria-hidden />;
}

async function stop(id?: string) {
  try {
    const r = await api.botStop(id);
    if (r.unconfirmed.length > 0)
      toast.warning(
        `Stopped, but ${r.unconfirmed.length} command(s) are not confirmed ended. See the inbox.`,
      );
  } catch (e) {
    fail(e);
  }
}

/** The controls of the bot above the thread: state, stop, pause automation and sleep the computer. */
export function BotBar({
  status,
  inboxCount,
  screenOpen,
  onScreen,
  onInbox,
  onHome,
}: {
  status: api.BotStatus;
  inboxCount: number;
  screenOpen: boolean;
  onScreen: () => void;
  onInbox: () => void;
  onHome: () => void;
}) {
  const botId = useContext(BotScope);
  const d = describeBot(status);
  const awake = status.computer === "awake";
  const working = !!status.working;
  return (
    <div
      role="region"
      aria-label="Bot"
      className="flex flex-wrap items-center gap-1.5 border-b px-2 py-1.5 text-xs md:px-4"
    >
      <button
        type="button"
        onClick={onHome}
        className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-muted"
        aria-label="Overview"
      >
        <StatusDot tone={d.tone} />
        <span className="font-medium">{d.label}</span>
      </button>
      {status.working && (
        <span className="text-muted-foreground min-w-0 max-w-[40ch] truncate">
          {status.working.title}
        </span>
      )}
      <span className="flex-1" />
      {working && (
        <Button size="sm" variant="destructive" onClick={() => stop(botId)}>
          <SquareIcon className="size-3" /> Stop
        </Button>
      )}
      <Button
        size="sm"
        variant="outline"
        onClick={() => api.botPause(!status.automation_paused,botId).catch(fail)}
        title="Pause automation: no new task starts. The computer keeps its state."
      >
        {status.automation_paused ? <PlayIcon className="size-3" /> : <PauseIcon className="size-3" />}
        {status.automation_paused ? "Resume automation" : "Pause automation"}
      </Button>
      <Button
        size="sm"
        variant="outline"
        disabled={status.computer === "waking" || status.computer === "saving"}
        onClick={() => (awake ? api.botSleep(botId) : api.botWake(botId)).catch(fail)}
        title={awake ? "Save the computer to disk. It wakes on the next task." : "Wake the computer"}
      >
        {awake ? <MoonIcon className="size-3" /> : <SunIcon className="size-3" />}
        {awake ? "Sleep computer" : "Wake computer"}
      </Button>
      {awake && (
        <Button size="sm" variant={screenOpen ? "secondary" : "outline"} onClick={onScreen}>
          <MonitorIcon className="size-3" /> Screen
        </Button>
      )}
      <Button size="sm" variant="outline" onClick={onInbox} aria-label="Inbox">
        <BellIcon className="size-3" />
        {inboxCount > 0 && <span className="tabular-nums">{inboxCount}</span>}
      </Button>
    </div>
  );
}

/**
 * The screen of the computer. The stream opens only while this is mounted, so a closed panel
 * costs nothing. The stream carries the frame number. The browser fetches the image.
 */
export function BotScreen() {
  const botId=useContext(BotScope);
  const [seq, setSeq] = useState(0);
  useEffect(() => {
    const es = new EventSource(api.botScreenUrl(botId));
    es.addEventListener("frame", (e) =>
      setSeq(JSON.parse((e as MessageEvent).data).seq),
    );
    return () => es.close();
  }, [botId]);
  return (
    <div className="bg-muted/40 mx-2 my-2 aspect-[16/10] max-h-[45dvh] overflow-hidden rounded-xl border md:mx-4">
      {seq > 0 ? (
        <img
          src={api.botFrameUrl(seq,botId)}
          alt="Screen of the bot computer"
          className="size-full object-contain"
        />
      ) : (
        <div className="text-muted-foreground flex size-full items-center justify-center text-sm">
          Waiting for the first frame
        </div>
      )}
    </div>
  );
}

/** How a message from the composer reaches the bot. */
export function SendOptions({
  mode,
  priority,
  setMode,
  setPriority,
  working,
}: {
  mode: "steer" | "queue";
  priority: number;
  setMode: (m: "steer" | "queue") => void;
  setPriority: (p: number) => void;
  working: boolean;
}) {
  return (
    <div className="text-muted-foreground flex items-center gap-2 px-3 pb-1 text-xs">
      <label className="flex items-center gap-1">
        Send as
        <select
          className="bg-background rounded border px-1 py-0.5"
          value={mode}
          onChange={(e) => setMode(e.target.value as "steer" | "queue")}
        >
          <option value="steer">{working ? "Steer the current task" : "Do now"}</option>
          <option value="queue">Queue as a new task</option>
        </select>
      </label>
      {mode === "queue" && (
        <label className="flex items-center gap-1">
          Priority
          <select
            className="bg-background rounded border px-1 py-0.5"
            value={priority}
            onChange={(e) => setPriority(Number(e.target.value))}
          >
            {PRIORITIES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}

/** The owner's answer to a request to raise the spending ceiling. */
function CeilingForm({ r, done }: { r: api.CeilingRequest; done: () => void }) {
  const botId=useContext(BotScope);
  const [amount, setAmount] = useState("");
  const send = (decision: "approve" | "deny") => {
    const a = ceilingAnswer(r, decision, amount, crypto.randomUUID());
    if (!a) return toast.error("Enter an amount above the current ceiling.");
    api.botAnswerCeiling(a,botId).then(done, fail);
  };
  const shown = (r.current_ceiling_minor / 10 ** r.precision).toFixed(r.precision);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-muted-foreground text-xs">
        Ceiling now {shown} {r.currency}. {r.reason}
      </span>
      <input
        className="bg-background w-28 rounded border px-1 py-0.5 text-xs"
        inputMode="decimal"
        aria-label={`New ceiling in ${r.currency}`}
        placeholder={`New ceiling (${r.currency})`}
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
      />
      <Button size="sm" onClick={() => send("approve")}>
        Raise the ceiling
      </Button>
      <Button size="sm" variant="outline" onClick={() => send("deny")}>
        Keep the ceiling
      </Button>
    </div>
  );
}

const ACTIONS: Record<string, (i: api.InboxItem, done: () => void, botId?:string) => React.ReactNode> = {
  unknown_outcome: (i, done, botId) => (
    <div className="flex flex-wrap gap-2">
      {[
        ["ran", "It ran"],
        ["not_run", "It did not run"],
        ["dismissed", "Dismiss"],
      ].map(([d, label]) => (
        <Button
          key={d}
          size="sm"
          variant="outline"
          onClick={() => api.botDecide(i.id, d,"",botId).then(done, fail)}
        >
          {label}
        </Button>
      ))}
    </div>
  ),
  budget_hold: (_i, done, botId) => (
    <Button
      size="sm"
      variant="outline"
      title="Releases this hold only. It does not change the spending ceiling."
      onClick={() => api.botApprove(botId).then(done, fail)}
    >
      Release the hold
    </Button>
  ),
  ceiling_request: (i, done) => (i.ceiling ? <CeilingForm r={i.ceiling} done={done} /> : null),
  held_task: (i, done, botId) => (
    <div className="flex gap-2">
      <Button size="sm" variant="outline" onClick={() => api.botRetryTask(i.id,botId).then(done, fail)}>
        Retry
      </Button>
      <Button size="sm" variant="outline" onClick={() => api.botCancelTask(i.id,botId).then(done, fail)}>
        Cancel
      </Button>
    </div>
  ),
  waiting_task: (i, done, botId) => (
    <Button size="sm" variant="outline" onClick={() => api.botCancelTask(i.id,botId).then(done, fail)}>
      Cancel the task
    </Button>
  ),
};

/** Things that need the owner: unsettled command outcomes, holds, tasks that wait or were held. */
export function InboxPanel({
  items,
  refresh,
  onClose,
}: {
  items: api.InboxItem[];
  refresh: () => void;
  onClose: () => void;
}) {
  const botId=useContext(BotScope);
  return (
    <section aria-label="Inbox" className="border-b px-3 py-2 md:px-4">
      <div className="mb-2 flex items-center justify-between text-sm font-medium">
        Inbox
        <Button size="sm" variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      {items.length === 0 ? (
        <p className="text-muted-foreground text-sm">Nothing needs you.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((i) => (
            <li key={`${i.kind}-${i.id}`} className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">{i.title}</span>
              {i.detail && (
                <span className="text-muted-foreground break-words text-xs">{i.detail}</span>
              )}
              {ACTIONS[i.kind]?.(i, refresh, botId)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The home overview: one card for the bot. */
export function BotHome({
  status,
  inboxCount,
  onOpen,
}: {
  status: api.BotStatus;
  inboxCount: number;
  onOpen: () => void;
}) {
  const d = describeBot(status);
  const recent = status.tasks.slice(0, 4);
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-3 p-4">
      <button
        type="button"
        onClick={onOpen}
        className="bg-card hover:bg-muted/50 flex flex-col gap-2 rounded-2xl border p-4 text-left shadow-sm"
      >
        <span className="flex items-center gap-2 text-sm font-medium">
          <StatusDot tone={d.tone} /> Bot · {d.label}
        </span>
        <span className="text-muted-foreground text-xs">
          Computer: {status.computer}
          {status.idle_left_ms !== null && status.computer === "awake"
            ? ` · saves in ${Math.ceil(status.idle_left_ms / 1000)} s if idle`
            : ""}
          {status.queued > 0 ? ` · ${status.queued} queued` : ""}
          {inboxCount > 0 ? ` · ${inboxCount} need you` : ""}
        </span>
        {recent.length > 0 && (
          <ul className="text-xs">
            {recent.map((t) => (
              <li key={t.id} className="flex gap-2 truncate">
                <span className="text-muted-foreground w-16 shrink-0">{t.state}</span>
                <span className="truncate">{t.title}</span>
              </li>
            ))}
          </ul>
        )}
        <span className="text-xs underline">Open the chat</span>
      </button>
      {status.last_error && (
        <p role="alert" className="text-xs text-red-500">
          {status.last_error}
        </p>
      )}
    </div>
  );
}
