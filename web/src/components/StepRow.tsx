import {
  WorkLogButton,
  WorkLogDetails,
  WorkLogList,
  WorkLogRow,
} from "@/components/WorkLog";
import { formatElapsed } from "@/lib/elapsed";
import { describeStep } from "@/lib/steps";
import { cn } from "@/lib/utils";
import type { ThreadGroupPart } from "@/components/assistant-ui/elements/thread.aui";
import {
  useToolCallElapsed,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import {
  CameraIcon,
  CircleAlertIcon,
  CodeIcon,
  FileTextIcon,
  GlobeIcon,
  HandIcon,
  KeyboardIcon,
  KeyRoundIcon,
  LayersIcon,
  Loader2Icon,
  MousePointerClickIcon,
  PaperclipIcon,
  SearchIcon,
  WrenchIcon,
  type LucideIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState, type PropsWithChildren, type ReactNode } from "react";

const ICONS: Record<string, LucideIcon> = {
  open: GlobeIcon,
  goto: GlobeIcon,
  click: MousePointerClickIcon,
  type: KeyboardIcon,
  press: KeyboardIcon,
  snapshot: FileTextIcon,
  read: FileTextIcon,
  links: FileTextIcon,
  find: SearchIcon,
  screenshot: CameraIcon,
  eval: CodeIcon,
  ensure_session: KeyRoundIcon,
  save_state: KeyRoundIcon,
  send_file: PaperclipIcon,
};

function iconFor(name: string, args: Record<string, unknown>): LucideIcon {
  const tool = name.replace(/^browser_/, "");
  const key = tool === "act" ? String(args.op ?? "click") : tool;
  return ICONS[key] ?? WrenchIcon;
}

/** Expand and collapse with a spring on the height. */
function Reveal({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ type: "spring", stiffness: 420, damping: 38 }}
          className="overflow-hidden"
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Elapsed() {
  const ms = useToolCallElapsed();
  if (ms === undefined) return null;
  return (
    <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
      {formatElapsed(ms / 1000)}
    </span>
  );
}

/** One step of a run as a compact work-log row. A tap shows its input and result. */
export const StepRow: ToolCallMessagePartComponent = ({
  toolName,
  args,
  argsText,
  result,
  isError,
  status,
  approval,
}) => {
  const [open, setOpen] = useState(false);
  const running = status?.type === "running";
  const failed =
    isError || (status?.type === "incomplete" && status.reason !== "cancelled");
  const waiting = approval !== undefined && approval.approved === undefined;
  const Icon = iconFor(toolName, (args ?? {}) as Record<string, unknown>);
  const label = describeStep(toolName, args, running);

  return (
    <WorkLogRow
      role="button"
      tabIndex={0}
      aria-expanded={open}
      aria-label={failed ? `${label}, failed` : label}
      onClick={() => setOpen((o) => !o)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setOpen((o) => !o);
        }
      }}
      icon={
        running ? (
          <Loader2Icon
            className="text-muted-foreground size-4 animate-spin"
            aria-hidden
          />
        ) : failed ? (
          <CircleAlertIcon className="text-error size-4" aria-hidden />
        ) : waiting ? (
          <HandIcon className="text-warning size-4" aria-hidden />
        ) : (
          <Icon className="text-muted-foreground size-4" aria-hidden />
        )
      }
      label={
        <span
          className={cn(
            "block truncate",
            running && "live-tool-shine",
            failed && "text-error",
          )}
        >
          {label}
          {waiting && (
            <span className="text-warning ms-1.5 text-xs">
              needs your answer
            </span>
          )}
          {approval?.approved === false && (
            <span className="text-muted-foreground ms-1.5 text-xs">denied</span>
          )}
        </span>
      }
      trailing={<Elapsed />}
    >
      <Reveal open={open}>
        <WorkLogDetails>
          <pre className="bg-muted/60 overflow-x-auto rounded-md p-2 font-mono text-xs whitespace-pre-wrap">
            {argsText || "{}"}
          </pre>
          {result !== undefined && (
            <pre
              className={cn(
                "bg-muted/60 max-h-60 overflow-auto rounded-md p-2 font-mono text-xs whitespace-pre-wrap",
                failed && "text-error",
              )}
            >
              {typeof result === "string"
                ? result
                : JSON.stringify(result, null, 2)}
            </pre>
          )}
        </WorkLogDetails>
      </Reveal>
    </WorkLogRow>
  );
};

/** Consecutive steps: one header row, such as "Worked for 12s · 4 steps". It opens while the run works. */
export function StepGroup({
  group,
  children,
}: PropsWithChildren<{ group: ThreadGroupPart }>) {
  const active = group.status.type === "running";
  const [open, setOpen] = useState<boolean | null>(null);
  const shown = open ?? active;
  const count = group.indices.length;
  const t = group.timing;
  const secs =
    t?.completedAt === undefined
      ? undefined
      : (t.completedAt - t.startedAt) / 1000;
  const steps = `${count} ${count === 1 ? "step" : "steps"}`;
  const label = active
    ? `Working · ${steps}`
    : secs === undefined
      ? steps
      : `Worked for ${formatElapsed(secs)} · ${steps}`;

  return (
    <div className="my-1">
      <WorkLogButton
        aria-expanded={shown}
        onClick={() => setOpen(!shown)}
        icon={
          <LayersIcon className="text-muted-foreground size-4" aria-hidden />
        }
        label={
          <span className={cn("block truncate", active && "live-tool-shine")}>
            {label}
          </span>
        }
      />
      <Reveal open={shown}>
        <div className="border-border ms-3 border-s ps-2">
          <WorkLogList>{children}</WorkLogList>
        </div>
      </Reveal>
    </div>
  );
}
