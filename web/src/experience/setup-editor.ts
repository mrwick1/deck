import type { SetupDraft, SetupRecord } from "../lib/api";
import { toFields, sameFields, type Fields } from "./setup-form";
import type { Result } from "./actions";

export type Phase = "idle" | "saving" | "approving" | "checking" | "approved";
export type Action = "save" | "approve";
export interface Notice { kind: "success" | "error"; text: string }
/** A write whose outcome the owner does not know. Save and Approve stay blocked until a read settles it. */
export interface Unconfirmed { stage: "save" | "approve"; base: SetupRecord; submitted: SetupDraft; saved: SetupRecord | null }
export interface Editor {
  /** The server row these fields started from. Saves send its version. */
  base: SetupRecord;
  fields: Fields;
  /** A newer server row that arrived while the fields held unsaved edits. */
  conflict: SetupRecord | null;
  /** What this editor's own write will look like when the server confirms it. */
  expect: { version: number; revision: number; fields: Fields } | null;
}
export interface Entry { editor: Editor; phase: Phase; action: Action; notice: Notice | null; unconfirmed: Unconfirmed | null }
export type Entries = Record<string, Entry>;

export const openEditor = (row: SetupRecord): Editor => ({ base: row, fields: toFields(row.setup.draft), conflict: null, expect: null });
export const newEntry = (row: SetupRecord): Entry => ({ editor: openEditor(row), phase: "idle", action: "save", notice: null, unconfirmed: null });
export const isDirty = (editor: Editor) => !sameFields(editor.fields, toFields(editor.base.setup.draft));
export const isBusy = (entry: Entry) => entry.phase !== "idle";
/** Anything the owner would lose on reload: edits, a running write or an unconfirmed one. */
export const hasUnsaved = (entry: Entry) => isDirty(entry.editor) || isBusy(entry) && entry.phase !== "approved" || !!entry.unconfirmed;

/** Apply a polled row. A clean editor adopts it. A dirty editor keeps every field and records the conflict. */
export function receive(editor: Editor, row: SetupRecord): Editor {
  if (row.version <= editor.base.version) return editor;
  const { expect } = editor;
  if (expect && row.version === expect.version && row.setup.draft.revision === expect.revision && sameFields(toFields(row.setup.draft), expect.fields)) return openEditor(row);
  if (!isDirty(editor)) return openEditor(row);
  return editor.conflict?.version === row.version ? editor : { ...editor, conflict: row };
}
/** Add the row to the store, or update its editor. A row that left Draft drops its editor. */
export function receiveRow(entries: Entries, row: SetupRecord): Entries {
  const id = row.setup.id, entry = entries[id];
  if (row.phase !== "Draft") { if (!entry) return entries; const { [id]: _, ...rest } = entries; return rest; }
  if (!entry) return { ...entries, [id]: newEntry(row) };
  const editor = receive(entry.editor, row);
  return editor === entry.editor ? entries : { ...entries, [id]: { ...entry, editor } };
}

export const edit = (entry: Entry, patch: Partial<Fields>): Entry => ({ ...entry, editor: { ...entry.editor, fields: { ...entry.editor.fields, ...patch } }, notice: entry.notice?.kind === "success" ? null : entry.notice });
export const begin = (entry: Entry, action: Action, submitted: SetupDraft): Entry => ({ ...entry, action, phase: "saving", notice: null, unconfirmed: null, editor: { ...entry.editor, expect: { version: entry.editor.base.version + 1, revision: entry.editor.base.setup.draft.revision + 1, fields: toFields(submitted) } } });
/** Explicit reload or discard. It resets even when the server version did not change. */
export const reset = (entry: Entry, row: SetupRecord): Entry => entry.unconfirmed ? entry : ({ ...newEntry(row), phase: entry.phase === "approved" ? "approved" : "idle" });
/** The owner keeps their edits and replaces the newer server row on the next save. */
export const keepMine = (entry: Entry): Entry => entry.editor.conflict ? { ...entry, editor: { ...entry.editor, base: entry.editor.conflict, conflict: null, expect: null } } : entry;

const errorText = (error: unknown) => error instanceof Error ? error.message : "The server did not confirm the change.";
const done = (entry: Entry, notice: Notice | null): Entry => ({ ...entry, phase: "idle", notice, unconfirmed: null });
/** Adopt a confirmed row, or keep the owner's fields as a conflict when the row is not the one they wrote. */
const adopt = (entry: Entry, row: SetupRecord | null) => row ? { ...entry, editor: receive(entry.editor, row) } : entry;

/** Fold the outcome of one submit into the entry. Success text appears only after the server confirms it. */
export function applyResult(entry: Entry, result: Result, base: SetupRecord, submitted: SetupDraft): Entry {
  const settled = (row: SetupRecord | null, notice: Notice) => done(adopt(entry, row), notice);
  switch (result.kind) {
    case "saved": {
      const text = result.recovered ? "The connection dropped, but the server confirms the draft was saved." : "Draft saved.";
      return settled(result.row, { kind: "success", text: entry.action === "approve" && result.recovered ? `${text} Approval was not sent. Review it, then approve again.` : text });
    }
    case "approved": return { ...done(entry, { kind: "success", text: result.recovered ? "The connection dropped, but the server confirms approval. Deck is preparing the computer." : "Approved. Deck is preparing the computer." }), phase: "approved" };
    case "conflict": return done({ ...entry, editor: { ...entry.editor, expect: null, conflict: result.row.version > entry.editor.base.version ? result.row : entry.editor.conflict } }, { kind: "error", text: "The setup changed on the server. Nothing was approved. Your edits are kept." });
    case "unconfirmed": return { ...entry, phase: "idle", unconfirmed: { stage: result.stage, base, submitted, saved: result.saved }, notice: { kind: "error", text: result.stage === "save" ? "Deck could not confirm whether the draft was saved. Check the saved state before trying again." : "Deck could not confirm whether approval happened. Check the saved state before trying again." } };
    case "failed": {
      const cleared = { ...entry.editor, expect: result.stage === "save" ? null : entry.editor.expect };
      const next = result.stage === "approve" ? adopt({ ...entry, editor: cleared }, result.saved) : { ...entry, editor: cleared };
      return done(next, { kind: "error", text: result.stage === "approve" ? `Draft saved, but approval did not happen: ${errorText(result.error)}` : `Not saved: ${errorText(result.error)}` });
    }
  }
}
