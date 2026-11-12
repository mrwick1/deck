// The docked panel follows T3 Code's composer banners (MIT, see web/THIRD_PARTY.md).
import { Button } from "@/components/ui/button";
import { formatElapsed } from "@/lib/elapsed";
import { describeStep } from "@/lib/steps";
import type { PendingApproval } from "@/lib/useDeck";
import { HandIcon, Loader2Icon } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { createContext, use, useEffect, useState } from "react";

export interface DockState {
  running: boolean;
  /** Epoch milliseconds when the run started. */
  startedAt?: number;
  approval?: PendingApproval;
  answer: (approvalId: string, approved: boolean) => Promise<void>;
}

export const DockContext = createContext<DockState>({
  running: false,
  answer: async () => {},
});

function useNow(on: boolean) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [on]);
  return now;
}

/** A surface that tucks under the composer's top edge and slides out from behind it. */
function Docked({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <motion.div
      key={id}
      layout
      initial={{ y: 24, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      exit={{ y: 24, opacity: 0 }}
      transition={{ type: "spring", stiffness: 420, damping: 36 }}
      className="bg-card/90 shadow-composer relative mx-auto -mb-4 w-[calc(100%-1.5rem)] rounded-t-2xl border border-b-0 px-3 pt-2.5 pb-6 backdrop-blur-md"
    >
      {children}
    </motion.div>
  );
}

function ApprovalPanel({ approval }: { approval: PendingApproval }) {
  const { answer } = use(DockContext);
  const [busy, setBusy] = useState(false);
  const respond = async (ok: boolean) => {
    setBusy(true);
    try {
      await answer(approval.id, ok);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div role="group" aria-label="Approval" className="flex flex-col gap-2">
      <div className="text-muted-foreground flex items-center gap-2 text-2xs">
        <HandIcon className="text-warning size-3.5" aria-hidden />
        <span className="text-warning font-medium">Needs your answer</span>
      </div>
      <p className="text-sm font-medium">
        {describeStep(approval.name, approval.args, true)}
      </p>
      {approval.reason && (
        <p className="text-muted-foreground text-sm">{approval.reason}</p>
      )}
      <div className="flex justify-end gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => respond(false)}
        >
          Deny
        </Button>
        <Button size="sm" disabled={busy} onClick={() => respond(true)}>
          Approve
        </Button>
      </div>
    </div>
  );
}

function ActivityRow({ startedAt }: { startedAt?: number }) {
  const now = useNow(true);
  return (
    <div
      role="status"
      className="text-muted-foreground flex items-center gap-2 text-xs"
    >
      <Loader2Icon className="size-3.5 animate-spin" aria-hidden />
      <span className="live-tool-shine">Working</span>
      {startedAt !== undefined && (
        <span className="tabular-nums">
          {formatElapsed((now - startedAt) / 1000)}
        </span>
      )}
    </div>
  );
}

/** What sits on top of the composer: an approval, or the run's activity. */
export function ComposerDock() {
  const { running, startedAt, approval } = use(DockContext);
  return (
    <AnimatePresence initial={false} mode="popLayout">
      {approval ? (
        <Docked id={`approval-${approval.id}`}>
          <ApprovalPanel approval={approval} />
        </Docked>
      ) : running ? (
        <Docked id="activity">
          <ActivityRow startedAt={startedAt} />
        </Docked>
      ) : null}
    </AnimatePresence>
  );
}
