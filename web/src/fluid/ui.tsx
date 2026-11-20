// Shared parts of the fluid look: springs, status dots, tooltips, menus and task icons.
// The parts read the tokens of `fluid.css`. Put them inside an element with the `fluid` class.
import type { ReactNode } from "react";
import { motion } from "motion/react";
import { Check, Clock, Pause, X } from "lucide-react";
import { Tooltip } from "@animate-ui/react/primitives/radix-ui";
import { ShimmeringText } from "@animate-ui/react/primitives/texts/shimmering";
import { cn } from "@/lib/cn";
import type { BotTask } from "@/lib/api";
import type { Tone } from "@/lib/bot";
import "./fluid.css";

export const spring = { type: "spring", stiffness: 380, damping: 32, mass: 0.8 } as const;
export const soft = { type: "spring", stiffness: 260, damping: 28 } as const;

/** Menu surfaces. Portals render outside the page root, so the content repeats the `fluid` class. */
export const menuContent = "fluid z-50 min-w-52 rounded-[16px] border border-border bg-popover p-1.5 shadow-2xl";
export const menuItem = "flex min-h-9 cursor-default items-center gap-2 rounded-[12px] px-3 text-sm outline-none data-highlighted:bg-accent data-disabled:opacity-40 [&_svg]:size-4";
export const iconButton = "grid size-9 shrink-0 place-items-center rounded-[12px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40";
export const pill = "pill inline-flex h-9 items-center justify-center gap-2 px-4 text-sm transition-colors disabled:pointer-events-none disabled:opacity-40";

const toneDot: Record<Tone, string> = { busy: "bg-info breathe", attention: "bg-warning", error: "bg-brand", idle: "bg-success", asleep: "bg-faint-foreground" };

export function Dot({ tone, className }: { tone: Tone; className?: string }) {
  return <span aria-hidden="true" className={cn("relative inline-block size-2 shrink-0 rounded-full", toneDot[tone], className)} />;
}

export function Tip({ label, children }: { label: string; children: ReactNode }) {
  return <Tooltip.Root><Tooltip.Trigger asChild>{children}</Tooltip.Trigger><Tooltip.Portal><Tooltip.Content sideOffset={8} className="z-50 rounded-[10px] bg-foreground px-2.5 py-1.5 text-xs font-medium text-background shadow-lg">{label}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>;
}

const taskTone: Record<BotTask["state"], string> = { done: "bg-success/15 text-success", failed: "bg-brand/15 text-brand", running: "bg-info/15 text-info", queued: "bg-accent text-muted-foreground", waiting: "bg-warning/15 text-warning", held: "bg-warning/15 text-warning", cancelled: "bg-accent text-faint-foreground" };
const taskLabel: Record<BotTask["state"], string> = { done: "Done", failed: "Failed", running: "Running", queued: "Queued", waiting: "Waiting", held: "Held", cancelled: "Cancelled" };

/** A round task icon. Screen readers get the state as text. */
export function TaskIcon({ state }: { state: BotTask["state"] }) {
  return <span className={cn("relative grid size-6 shrink-0 place-items-center rounded-full [&_svg]:size-3.5", taskTone[state])}>
    {state === "done" ? <Check aria-hidden="true" /> : state === "failed" || state === "cancelled" ? <X aria-hidden="true" /> : state === "running" ? <Dot tone="busy" className="size-1.5" /> : state === "held" ? <Pause aria-hidden="true" /> : <Clock aria-hidden="true" />}
    <span className="sr-only">{taskLabel[state]}: </span>
  </span>;
}

/** A task title. A running task shimmers. */
export function TaskTitle({ task, className }: { task: BotTask; className?: string }) {
  return task.state === "running" ? <ShimmeringText className={className} text={task.title} duration={1.6} /> : <span className={className}>{task.title}</span>;
}

/** A soft section with a title, an optional count and an optional note. */
export function Section({ id, title, count, note, children }: { id: string; title: string; count?: number; note?: ReactNode; children: ReactNode }) {
  return <section className="flex flex-col gap-4" aria-labelledby={id}>
    <div className="flex items-baseline gap-3"><h2 id={id} className="text-lg font-medium">{title}</h2>
      {count !== undefined && count > 0 && <motion.span key={count} initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={spring} className="rounded-full bg-raised px-2 py-0.5 text-xs text-muted-foreground tabular-nums">{count}</motion.span>}
      {note && <p className="text-sm text-muted-foreground">{note}</p>}</div>
    {children}
  </section>;
}

export const toasterOptions = { className: "!rounded-[16px] !border-border !bg-popover !text-foreground" };
