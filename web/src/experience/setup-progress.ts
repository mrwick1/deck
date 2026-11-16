import type { SetupRecord } from "../lib/api";

export const U32_MAX = 4294967295;
/** One key for one setup. Version updates never remount the screen, so typing, focus and scroll stay. */
export const setupKey = (row: SetupRecord) => row.setup.id;
/** Draft keeps the review title. Every later phase reads as progress. */
export const setupHeading = (row?: SetupRecord) => !row ? "Setup" : row.phase === "Draft" ? "Review setup" : "Setup progress";

/** The one label for a setup phase. Runtime readiness wins over the raw phase. */
export function phaseLabel(row: SetupRecord): string {
  if (row.runtime_ready) return "Your bot is ready";
  if (row.phase === "Ready") return "Preparing the runtime";
  if (row.phase === "Draft") return "Review setup";
  return row.phase;
}

export type Attempts = { ok: true; value: number } | { ok: false; message: string };
/** The new total must be a whole number above the current limit and inside the server u32 range. */
export function checkAttempts(text: string, current: number): Attempts {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return { ok: false, message: "Enter a whole number of attempts." };
  const value = Number(t);
  if (value <= current) return { ok: false, message: `Enter a number above ${current}.` };
  if (value > U32_MAX) return { ok: false, message: `Enter ${U32_MAX} or less.` };
  return { ok: true, value };
}

/** Why Resume is off, or null. Resume never clears an unknown outcome or an unsupported challenge. */
export function resumeBlock(row: SetupRecord): string | null {
  if (row.phase === "Investigate") return "Resume is off. The last step has an unknown outcome.";
  const kind = row.setup.wait?.kind;
  if (typeof kind === "object" && "Unsupported" in kind) return "Resume is off. Deck cannot complete this challenge, so resuming would not resolve it.";
  return null;
}

/** Show Pause unless the bot is known to be paused. Show Resume unless it is known to run. Unknown shows both. */
export const pauseActions = (paused?: boolean) => ({ pause: paused !== true, resume: paused !== false });

const stepName = (step: unknown) => typeof step === "string" ? step : step && typeof step === "object" ? Object.entries(step).map(([k, v]) => `${k} ${String(v)}`).join(", ") : "Step";
/** Readable lines for steps that are not done. Text only. */
export const stepLines = (row: SetupRecord) => row.setup.steps.filter(s => s.status !== "Done").map(s => `${stepName(s.step)}: ${s.status}${s.last_error ? `. ${s.last_error.slice(0, 300)}` : ""}`);
