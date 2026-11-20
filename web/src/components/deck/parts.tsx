import { useLayoutEffect, useRef, type ComponentProps, type ReactNode } from "react";
import { ArrowUp, Check, ChevronRight, Clock, Monitor, Pause, TriangleAlert, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/button";
import type { Tone } from "@/lib/bot";
import type { BotTask } from "@/lib/api";

// Each tone has a color and a text label. Color is never the only signal.
const toneText: Record<Tone, string> = {
  busy: "text-foreground",
  attention: "text-warning-foreground",
  error: "text-error-foreground",
  idle: "text-foreground",
  asleep: "text-muted-foreground",
};
const toneDot: Record<Tone, string> = {
  busy: "bg-info deck-pulse",
  attention: "bg-warning",
  error: "bg-error",
  idle: "bg-success",
  asleep: "bg-faint-foreground/60",
};

export function StatusDot({ tone, className }: { tone: Tone; className?: string }) {
  return <span aria-hidden="true" data-tone={tone} className={cn("relative inline-block size-1.5 shrink-0 rounded-full", toneDot[tone], className)} />;
}

export function Status({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
  return <span className={cn("inline-flex min-w-0 items-center gap-2 text-ui", toneText[tone], className)}><StatusDot tone={tone} /><span className="truncate">{children}</span></span>;
}

const numerals = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];
/** A section label in the portfolio style: a red numeral, a mono label, a hairline and an optional count. */
export function SectionHeader({ index, label, count, action, id }: { index?: number; label: string; count?: number; action?: ReactNode; id?: string }) {
  return <div className="flex h-8 items-center gap-3">
    {index !== undefined && <span aria-hidden="true" className="w-5 font-display text-sm text-brand italic">{numerals[index] ?? index + 1}</span>}
    <h2 id={id} className="label-mono">{label}</h2>
    {count !== undefined && <span className="font-mono text-2xs text-muted-foreground tabular-nums">{count}</span>}
    <span aria-hidden="true" className="h-px flex-1 bg-border" />
    {action}
  </div>;
}

/** A name, a dotted leader and a value, on one line. */
export function LeaderRow({ name, detail, value, className }: { name: ReactNode; detail?: ReactNode; value: ReactNode; className?: string }) {
  return <div className={cn("flex min-w-0 items-baseline gap-3 text-ui", className)}>
    <span className="min-w-0 truncate font-medium text-foreground">{name}</span>
    {detail && <span className="truncate text-muted-foreground">{detail}</span>}
    <span aria-hidden="true" className="leader" />
    <span className="shrink-0 font-mono text-2xs text-muted-foreground tabular-nums">{value}</span>
  </div>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-border bg-raised px-1 font-mono text-2xs text-muted-foreground">{children}</kbd>;
}

const rowBase = "group flex w-full min-w-0 items-center gap-3 border-t border-border px-4 text-left outline-none transition-colors duration-(--duration-fast) first:border-t-0 focus-visible:bg-accent focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring hover:bg-accent/60 aria-selected:bg-accent aria-[current=true]:bg-accent";

export type BotSummary = { id: string; name: string; job?: string; tone: Tone; status: string; now?: string | null; queued?: number; computer?: string };

/** A dense bot row for lists of any length. */
export function BotRow({ bot, selected, ...props }: { bot: BotSummary; selected?: boolean } & ComponentProps<"button">) {
  return <button type="button" aria-current={selected || undefined} className={cn(rowBase, "h-14")} {...props}>
    <StatusDot tone={bot.tone} />
    <span className="flex min-w-0 flex-1 flex-col">
      <span className="truncate text-ui font-medium">{bot.name}</span>
      <span className={cn("truncate text-xs text-muted-foreground", bot.now && bot.tone === "busy" && "live-tool-shine")}>{bot.now ?? bot.status}</span>
    </span>
    {!!bot.queued && <span className="font-mono text-2xs text-muted-foreground tabular-nums">{bot.queued} queued</span>}
    <ChevronRight aria-hidden="true" className="size-4 text-faint-foreground transition-transform duration-(--duration-fast) ease-(--ease-out) group-hover:translate-x-0.5" />
  </button>;
}

/** A bot card for small fleets: status, what it does now, its last tasks and the computer. */
export function BotCard({ bot, tasks, onOpen, action }: { bot: BotSummary; tasks: BotTask[]; onOpen: () => void; action?: ReactNode }) {
  return <article className={cn("flex min-w-0 flex-col border border-border bg-card transition-colors duration-(--duration-fast) hover:border-input", bot.tone === "attention" && "border-warning/40")}>
    <header className="flex items-start gap-3 p-4 pb-3">
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <h3 className="truncate text-sm font-medium">{bot.name}</h3>
        <Status tone={bot.tone}>{bot.status}</Status>
      </span>
    </header>
    <div className="mx-4 min-h-16 border-l-2 border-border bg-surface px-3 py-2" data-tone={bot.tone}>
      <p className="label-mono">{bot.now ? "Now" : "Last result"}</p>
      <p className={cn("mt-1 line-clamp-2 text-ui", bot.now && "live-tool-shine")}>{bot.now ?? tasks.find(task => task.state === "done")?.title ?? "No tasks yet"}</p>
    </div>
    <ul aria-label="Recent tasks" className="flex flex-col gap-1.5 px-4 pt-3 pb-1">
      {tasks.filter(task => task.state !== "running").slice(0, 3).map(task => <TaskLine key={task.id} task={task} compact />)}
    </ul>
    <footer className="mt-auto flex items-center gap-2 border-t border-border py-2 pr-2 pl-4">
      <span className="inline-flex items-center gap-1.5 font-mono text-2xs text-muted-foreground"><Monitor aria-hidden="true" className="size-3.5" />{bot.computer}</span>
      {!!bot.queued && <span className="font-mono text-2xs text-muted-foreground tabular-nums">· {bot.queued} queued</span>}
      <span className="flex-1" />
      {action}
      <Button variant="ghost" size="sm" onClick={onOpen}>Open<ChevronRight data-icon="inline-end" /></Button>
    </footer>
  </article>;
}

const taskIcon: Record<BotTask["state"], ReactNode> = {
  done: <Check className="size-3.5 text-success-foreground" />,
  running: <StatusDot tone="busy" className="mx-1" />,
  queued: <Clock className="size-3.5 text-muted-foreground" />,
  held: <Pause className="size-3.5 text-warning-foreground" />,
  waiting: <TriangleAlert className="size-3.5 text-warning-foreground" />,
  failed: <X className="size-3.5 text-error-foreground" />,
  cancelled: <X className="size-3.5 text-muted-foreground" />,
};
const taskLabel: Record<BotTask["state"], string> = { queued: "Queued", held: "On hold", waiting: "Waiting", running: "Working", done: "Completed", failed: "Failed", cancelled: "Cancelled" };

/** One task with its state. The state has an icon and, outside compact lists, a word. */
export function TaskLine({ task, compact }: { task: BotTask; compact?: boolean }) {
  return <li className={cn("flex min-w-0 items-center gap-2.5", compact ? "text-xs" : "py-2.5 text-ui")}>
    <span aria-hidden="true" className="grid size-4 shrink-0 place-items-center">{taskIcon[task.state]}</span>
    <span className={cn("min-w-0 flex-1 truncate", task.state === "running" && "live-tool-shine")}>{task.title}</span>
    <span className={cn("shrink-0 font-mono text-2xs text-muted-foreground", compact && "sr-only")}>{taskLabel[task.state]}</span>
  </li>;
}

/** Something the owner must answer. The red edge marks it; the copy says what and why. */
export function RequestCard({ bot, what, why, action, onAct }: { bot: string; what: string; why?: string; action: string; onAct: () => void }) {
  return <article className="deck-enter flex min-w-0 flex-col gap-2 border border-border border-l-2 border-l-brand bg-card p-4">
    <p className="label-mono truncate">{bot}</p>
    <p className="text-sm text-foreground">{what}</p>
    {why && <p className="text-ui text-muted-foreground">{why}</p>}
    <div className="mt-1"><Button size="sm" variant="outline" onClick={onAct}>{action}<ChevronRight data-icon="inline-end" /></Button></div>
  </article>;
}

/** The message box. The destination sits above the text, so the owner sees where a message goes before sending. */
export function Composer({ value, onChange, onSubmit, placeholder, label, destination, hint, busy, disabled }: {
  value: string; onChange: (value: string) => void; onSubmit: () => void; placeholder: string; label: string;
  destination?: ReactNode; hint?: ReactNode; busy?: boolean; disabled?: boolean;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = input.current;
    if (!element || CSS.supports("field-sizing", "content")) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(180, element.scrollHeight)}px`;
  }, [value]);
  return <form className="flex flex-col gap-2" onSubmit={event => { event.preventDefault(); if (value.trim() && !busy && !disabled) onSubmit(); }}>
    {destination}
    <div className="flex items-end gap-2 border border-input bg-raised py-2 pr-2 pl-3 transition-[border-color,box-shadow] duration-(--duration-base) focus-within:border-brand/60 focus-within:shadow-[0_0_0_3px_color-mix(in_srgb,var(--brand)_14%,transparent)]">
      <textarea ref={input} rows={1} maxLength={20000} aria-label={label} placeholder={placeholder} value={value} disabled={disabled} aria-busy={busy}
        onChange={event => onChange(event.target.value)}
        onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }}
        className="max-h-45 min-h-8 flex-1 resize-none bg-transparent py-1.5 text-sm leading-5 outline-none [field-sizing:content] placeholder:text-muted-foreground disabled:opacity-50" />
      <Button type="submit" size="icon" aria-label="Send" disabled={busy || disabled || !value.trim()}><ArrowUp /></Button>
    </div>
    {hint && <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground [&>*:first-child]:min-w-0 [&>*:first-child]:truncate [&>*:last-child]:shrink-0 [&>*:last-child]:whitespace-nowrap">{hint}</div>}
  </form>;
}
