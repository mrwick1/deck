import * as api from "../lib/api";
import { sameFields, toFields } from "./setup-form";

export { readAmount } from "./setup-form";

const CHANGED = "The setup changed. Reload and review it before approval.";
/** A write whose reply was lost may still have happened. Only a read can tell. */
const ambiguous = (error: unknown) => !(error instanceof api.ApiError) || error.status === 0 || error.status >= 500;
const sameDraft = (a: api.SetupDraft, b: api.SetupDraft) => sameFields(toFields(a), toFields(b));

export type Stage = "save" | "approve";
export type Result =
  | { kind: "saved"; row: api.SetupRecord | null; recovered?: boolean }
  | { kind: "approved"; recovered?: boolean }
  /** The server refused. A save stage change nothing. An approve stage left the draft saved. */
  | { kind: "failed"; stage: Stage; error: unknown; saved: api.SetupRecord | null }
  /** The server holds a row that is not the one the owner submitted. */
  | { kind: "conflict"; row: api.SetupRecord }
  /** The reply was lost and the read that would settle it also failed. */
  | { kind: "unconfirmed"; stage: Stage; saved: api.SetupRecord | null };

async function readSetup(id: string) {
  return (await api.listSetups()).find(item => item.setup.id === id);
}

/**
 * Read-only check after a lost reply. It compares the server row with the submitted
 * and base snapshots. It never writes, so it cannot replay an approval.
 */
export async function reconcile(base: api.SetupRecord, submitted: api.SetupDraft, stage: Stage, saved: api.SetupRecord | null): Promise<Result> {
  let current: api.SetupRecord | undefined;
  try { current = await readSetup(base.setup.id); } catch { return { kind: "unconfirmed", stage, saved }; }
  if (!current) return { kind: "unconfirmed", stage, saved };
  if (stage === "save") {
    if (current.version === base.version) return { kind: "failed", stage, error: new Error("The server shows no change. Your edits are kept."), saved: null };
    const mine = current.version === base.version + 1 && current.phase === "Draft" && current.setup.draft.revision === base.setup.draft.revision + 1 && sameDraft(current.setup.draft, submitted);
    return mine ? { kind: "saved", row: current, recovered: true } : { kind: "conflict", row: current };
  }
  const target = saved ?? base;
  if (current.version === target.version && current.phase === "Draft") return { kind: "failed", stage, error: new Error("The server shows the draft is not approved."), saved };
  const approved = current.phase !== "Draft" && current.version > target.version && current.setup.draft.revision === target.setup.draft.revision && sameDraft(current.setup.draft, target.setup.draft);
  return approved ? { kind: "approved", recovered: true } : { kind: "conflict", row: current };
}

/**
 * Save the draft. Approval then reads the saved row, checks it is exactly the submitted
 * revision and approves that revision. `onPhase` reports when approval starts.
 */
export async function submitReview(row: api.SetupRecord, draft: api.SetupDraft, approve: boolean, onPhase: (phase: "approving") => void = () => {}): Promise<Result> {
  try { await api.editSetup(row.setup.id, row.version, draft); }
  catch (error) { return ambiguous(error) ? reconcile(row, draft, "save", null) : { kind: "failed", stage: "save", error, saved: null }; }
  let saved: api.SetupRecord | undefined;
  try { saved = await readSetup(row.setup.id); }
  catch (error) { return approve ? { kind: "failed", stage: "approve", error, saved: null } : { kind: "saved", row: null }; }
  if (!approve) return { kind: "saved", row: saved ?? null };
  if (!saved) return { kind: "failed", stage: "approve", error: new Error(CHANGED), saved: null };
  const exact = saved.phase === "Draft" && saved.version === row.version + 1 && saved.setup.draft.revision === row.setup.draft.revision + 1 && sameDraft(saved.setup.draft, draft);
  if (!exact) return { kind: "conflict", row: saved };
  onPhase("approving");
  try { await api.approveSetup(saved.setup.id, saved.version, saved.setup.draft.revision); }
  catch (error) { return ambiguous(error) ? reconcile(row, draft, "approve", saved) : { kind: "failed", stage: "approve", error, saved }; }
  return { kind: "approved" };
}

/** Throwing form of `submitReview`. */
export async function saveReview(row: api.SetupRecord, draft: api.SetupDraft, approve: boolean) {
  const result = await submitReview(row, draft, approve);
  if (result.kind === "failed") throw result.error;
  if (result.kind === "conflict") throw new Error(CHANGED);
  if (result.kind === "unconfirmed") throw new Error("Setup was not confirmed. Refresh before trying again.");
}

export function destinationOptions(rows: api.SetupRecord[]) {
  return rows.filter(row => row.runtime_ready).map(row => ({ id: row.setup.id, chatId: row.chat_id, name: row.setup.draft.name }));
}
