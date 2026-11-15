import { describe, expect, it } from "vitest";
import type { SetupDraft, SetupRecord } from "../lib/api";
import type { Result } from "./actions";
import { applyResult, awaitingRead, begin, edit, hasUnsaved, without, isDirty, keepMine, newEntry, openEditor, receive, receiveRow, reset } from "./setup-editor";
import { validate } from "./setup-form";

const draft: SetupDraft = { revision: 3, name: "Mail", instructions: "Read mail", model: "configured", authority: "Full", resources: { cpus: 2, memory_mib: 1024, disk_gib: 8 }, budget: { currency: "USD", exponent: 2, period: "month", allowance: 0, ceiling: 0 }, credentials: [], task: null };
const rowAt = (version: number, patch: Partial<SetupDraft> = {}, phase: SetupRecord["phase"] = "Draft"): SetupRecord => ({ version, chat_id: 1, phase, runtime_ready: false, error: null, checklist: [], setup: { id: "bot-one", draft: { ...draft, revision: version - 1, ...patch }, max_attempts: 3, wait: null, steps: [] } });
const row = rowAt(4);

describe("editor against polled rows", () => {
  it("ignores a poll with the same or an older version, even with different content", () => {
    const editor = edit(newEntry(row), { name: "Mine" }).editor;
    expect(receive(editor, row)).toBe(editor);
    expect(receive(editor, rowAt(3, { name: "Old" }))).toBe(editor);
  });
  it("adopts a newer row when the editor is clean", () => {
    const next = receive(openEditor(row), rowAt(5, { name: "Theirs" }));
    expect(next.base.version).toBe(5);
    expect(next.fields.name).toBe("Theirs");
    expect(next.conflict).toBeNull();
  });
  it("keeps every field and records a conflict when a newer row arrives during unsaved edits", () => {
    const mine = edit(edit(newEntry(row), { instructions: "Mine, unsaved" }), { cpus: "6" }).editor;
    const next = receive(mine, rowAt(5, { name: "Theirs", instructions: "Theirs" }));
    expect(next.fields).toBe(mine.fields);
    expect(next.base.version).toBe(4);
    expect(next.conflict?.version).toBe(5);
    expect(receive(next, rowAt(5, { name: "Theirs" }))).toBe(next);
    expect(receive(next, rowAt(6)).conflict?.version).toBe(6);
  });
  it("treats edits undone by hand as clean", () => {
    const entry = edit(edit(newEntry(row), { name: "X" }), { name: "Mail" });
    expect(isDirty(entry.editor)).toBe(false);
    expect(receive(entry.editor, rowAt(5, { name: "Theirs" })).fields.name).toBe("Theirs");
  });
  it("recognises its own confirmed write and does not call it a conflict", () => {
    const edited = edit(newEntry(row), { name: "Mine" });
    const { draft: submitted } = validate(edited.editor.fields, row.setup.draft, false);
    const started = begin(edited, "save", submitted!);
    const confirmed = rowAt(5, { name: "Mine" });
    const next = receive(started.editor, confirmed);
    expect(next.conflict).toBeNull();
    expect(next.base.version).toBe(5);
    expect(isDirty(next)).toBe(false);
  });
  it("flags another client's write that races past the pending one", () => {
    const edited = edit(newEntry(row), { name: "Mine" });
    const started = begin(edited, "save", validate(edited.editor.fields, row.setup.draft, false).draft!);
    expect(receive(started.editor, rowAt(5, { name: "Theirs" })).conflict?.version).toBe(5);
  });
});

describe("editor store across navigation", () => {
  it("keeps unsaved edits in the store while no form is mounted, and flags later conflicts there", () => {
    let entries = receiveRow({}, row);
    entries = { ...entries, "bot-one": edit(entries["bot-one"], { instructions: "Half-written" }) };
    entries = receiveRow(entries, row);
    expect(entries["bot-one"].editor.fields.instructions).toBe("Half-written");
    entries = receiveRow(entries, rowAt(5, { name: "Theirs" }));
    expect(entries["bot-one"].editor.fields.instructions).toBe("Half-written");
    expect(entries["bot-one"].editor.conflict?.version).toBe(5);
    expect(hasUnsaved(entries["bot-one"])).toBe(true);
  });
  it("returns the same store object when nothing changed, so polls cause no render", () => {
    const entries = receiveRow({}, row);
    expect(receiveRow(entries, row)).toBe(entries);
  });
  it("drops the editor once the setup leaves Draft", () => {
    const entries = receiveRow(receiveRow({}, row), rowAt(6, {}, "Provisioning"));
    expect(entries).toEqual({});
  });
});

describe("explicit resolution", () => {
  const conflicted = () => receiveRow({ "bot-one": edit(newEntry(row), { name: "Mine" }) }, rowAt(5, { name: "Theirs" }))["bot-one"];
  it("discard resets to the given row even when the version is unchanged", () => {
    const entry = edit(newEntry(row), { name: "Mine" });
    const next = reset(entry, row);
    expect(next.editor.fields.name).toBe("Mail");
    expect(next.editor.base.version).toBe(4);
    expect(isDirty(next.editor)).toBe(false);
  });
  it("discard loads the newer row and clears the conflict and any notice", () => {
    const next = reset({ ...conflicted(), notice: { kind: "error", text: "x" } }, rowAt(5, { name: "Theirs" }));
    expect(next.editor.fields.name).toBe("Theirs");
    expect(next.editor.conflict).toBeNull();
    expect(next.notice).toBeNull();
  });
  it("keep mine rebases on the newer version without changing any field", () => {
    const entry = conflicted(), next = keepMine(entry);
    expect(next.editor.base.version).toBe(5);
    expect(next.editor.conflict).toBeNull();
    expect(next.editor.fields).toBe(entry.editor.fields);
    expect(isDirty(next.editor)).toBe(true);
  });
});

describe("outcomes", () => {
  const edited = edit(newEntry(row), { name: "Mine" });
  const submitted = validate(edited.editor.fields, row.setup.draft, true).draft!;
  const started = (action: "save" | "approve") => begin(edited, action, submitted);
  const fold = (action: "save" | "approve", result: Result) => applyResult(started(action), result, row, submitted);
  it("announces success only after a confirmed result and adopts the saved row", () => {
    expect(started("save").notice).toBeNull();
    const next = fold("save", { kind: "saved", row: rowAt(5, { name: "Mine" }) });
    expect(next.notice).toEqual({ kind: "success", text: "Draft saved." });
    expect(next.phase).toBe("idle");
    expect(next.editor.base.version).toBe(5);
  });
  it("saved without a read keeps the fields and waits for a poll to confirm", () => {
    const next = fold("save", { kind: "saved", row: null });
    expect(next.editor.base.version).toBe(4);
    expect(receive(next.editor, rowAt(5, { name: "Mine" })).conflict).toBeNull();
  });
  it("keeps the failure and the owner's text after a failed save", () => {
    const next = fold("save", { kind: "failed", stage: "save", error: new Error("Name too long"), saved: null });
    expect(next.notice).toEqual({ kind: "error", text: "Not saved: Name too long" });
    expect(next.editor.fields.name).toBe("Mine");
    expect(next.editor.expect).toBeNull();
  });
  it("reports partial success when the draft saved but approval failed", () => {
    const next = fold("approve", { kind: "failed", stage: "approve", error: new Error("limits"), saved: rowAt(5, { name: "Mine" }) });
    expect(next.notice?.text).toBe("Draft saved, but approval did not happen: limits");
    expect(next.editor.base.version).toBe(5);
    expect(isDirty(next.editor)).toBe(false);
  });
  it("blocks the form as unconfirmed until a read settles it", () => {
    const next = fold("save", { kind: "unconfirmed", stage: "save", saved: null });
    expect(next.unconfirmed).toMatchObject({ stage: "save", base: row });
    expect(next.notice?.kind).toBe("error");
    expect(next.phase).toBe("idle");
    expect(next.editor.fields.name).toBe("Mine");
  });
  it("a conflict keeps the edits, never says approved and records the row", () => {
    const next = fold("approve", { kind: "conflict", row: rowAt(6) });
    expect(next.editor.conflict?.version).toBe(6);
    expect(next.editor.fields.name).toBe("Mine");
    expect(next.phase).toBe("idle");
    expect(next.notice?.text).toContain("Nothing was approved");
  });
  it("approved locks the form and names the recovery when the reply was lost", () => {
    expect(fold("approve", { kind: "approved" }).phase).toBe("approved");
    expect(fold("approve", { kind: "approved", recovered: true }).notice?.text).toContain("connection dropped");
    expect(fold("approve", { kind: "saved", row: rowAt(5, { name: "Mine" }), recovered: true }).notice?.text).toContain("Approval was not sent");
  });
  it("a started write counts as unsaved so a reload warns", () => {
    expect(hasUnsaved(started("save"))).toBe(true);
    expect(hasUnsaved(newEntry(row))).toBe(false);
  });
});

describe("discard while a write is unsettled", () => {
  it("cannot clear an unconfirmed write by reloading the saved draft", () => {
    const pending = begin(edit(newEntry(row), { name: "Mine" }), "approve", { ...draft, name: "Mine" });
    const uncertain = applyResult(pending, { kind: "unconfirmed", stage: "approve", saved: null }, row, { ...draft, name: "Mine" });
    expect(reset(uncertain, row)).toBe(uncertain);
  });
});

describe("own write confirmed after the owner kept typing", () => {
  const typed = (extra: Partial<Parameters<typeof edit>[1]>) => edit(edit(newEntry(row), { name: "Mine" }), extra);
  const send = (entry: ReturnType<typeof newEntry>) => begin(entry, "save", validate(entry.editor.fields, row.setup.draft, false).draft!);
  const mine = rowAt(5, { name: "Mine" });
  it("saved row null, then typing, then the poll: keeps the new text and rebases", () => {
    const sent = send(edit(newEntry(row), { name: "Mine" }));
    const done = applyResult(sent, { kind: "saved", row: null }, row, validate(sent.editor.fields, row.setup.draft, false).draft!);
    const typing = edit(done, { instructions: "Z" });
    const next = receive(typing.editor, mine);
    expect(next.fields.instructions).toBe("Z");
    expect(next.fields.name).toBe("Mine");
    expect(next.base.version).toBe(5);
    expect(next.expect).toBeNull();
    expect(next.conflict).toBeNull();
    expect(isDirty(next)).toBe(true);
  });
  it("approve failed with saved null, then typing, then the poll: keeps the new text", () => {
    const sent = begin(edit(newEntry(row), { name: "Mine" }), "approve", validate(edit(newEntry(row), { name: "Mine" }).editor.fields, row.setup.draft, true).draft!);
    const failed = applyResult(sent, { kind: "failed", stage: "approve", error: new Error("read failed"), saved: null }, row, { ...draft, name: "Mine" });
    const next = receive(edit(failed, { task: "Z" }).editor, mine);
    expect(next.fields.task).toBe("Z");
    expect(next.base.version).toBe(5);
    expect(next.expect).toBeNull();
  });
  it("unconfirmed, then typing, then a check that finds the save: keeps the new text", () => {
    const submitted = validate(edit(newEntry(row), { name: "Mine" }).editor.fields, row.setup.draft, false).draft!;
    const sent = send(edit(newEntry(row), { name: "Mine" }));
    const unconfirmed = applyResult(sent, { kind: "unconfirmed", stage: "save", saved: null }, row, submitted);
    const typing = edit(unconfirmed, { instructions: "Z" });
    const checked = applyResult(typing, { kind: "saved", row: mine, recovered: true }, row, submitted);
    expect(checked.editor.fields.instructions).toBe("Z");
    expect(checked.editor.base.version).toBe(5);
    expect(checked.unconfirmed).toBeNull();
    const viaPoll = receive(typing.editor, mine);
    expect(viaPoll.fields.instructions).toBe("Z");
  });
  it("adopts the row when the fields still equal what was sent", () => {
    const sent = send(edit(newEntry(row), { name: "Mine" }));
    expect(isDirty(receive(sent.editor, mine))).toBe(false);
  });
  it("shows no unsaved edits while a sent write awaits the read, and unsaved edits once the owner types", () => {
    const sent = send(edit(newEntry(row), { name: "Mine" }));
    expect(isDirty(sent.editor)).toBe(false);
    expect(isDirty(edit(sent, { instructions: "Z" }).editor)).toBe(true);
  });
});

describe("remote phase change", () => {
  const left = rowAt(6, {}, "Provisioning");
  it("keeps a dirty editor and its fields", () => {
    const entries = receiveRow({ "bot-one": edit(newEntry(row), { instructions: "Half" }) }, left);
    expect(entries["bot-one"].editor.fields.instructions).toBe("Half");
    expect(hasUnsaved(entries["bot-one"])).toBe(true);
    expect(entries["bot-one"].left?.phase).toBe("Provisioning");
  });
  it("keeps an unconfirmed entry", () => {
    const base = edit(newEntry(row), { name: "Mine" });
    const uncertain = applyResult(begin(base, "save", { ...draft, name: "Mine" }), { kind: "unconfirmed", stage: "save", saved: null }, row, { ...draft, name: "Mine" });
    expect(receiveRow({ "bot-one": uncertain }, left)["bot-one"].unconfirmed).not.toBeNull();
  });
  it("drops a clean entry and a confirmed own approval", () => {
    expect(receiveRow({ "bot-one": newEntry(row) }, left)).toEqual({});
    expect(receiveRow({ "bot-one": { ...newEntry(row), phase: "approved" } }, left)).toEqual({});
  });
  it("does not say nothing was approved when the row left Draft", () => {
    const entry = begin(edit(newEntry(row), { name: "Mine" }), "approve", { ...draft, name: "Mine" });
    expect(applyResult(entry, { kind: "conflict", row: left }, row, { ...draft, name: "Mine" }).notice?.text).not.toContain("Nothing was approved");
  });
});

describe("recovery gates", () => {
  it("blocks a write while the base is stale and unblocks once the read confirms", () => {
    const sent = begin(edit(newEntry(row), { name: "Mine" }), "save", { ...draft, name: "Mine" });
    const settled = applyResult(sent, { kind: "saved", row: null }, row, { ...draft, name: "Mine" });
    expect(awaitingRead(settled)).toBe(true);
    expect(awaitingRead({ ...settled, editor: receive(settled.editor, rowAt(5, { name: "Mine" })) })).toBe(false);
  });
  it("clears the left marker if a Draft row returns, and keeps it on repeated polls", () => {
    const orphan = receiveRow({ "bot-one": edit(newEntry(row), { name: "Mine" }) }, rowAt(6, {}, "Provisioning"));
    expect(receiveRow(orphan, rowAt(6, {}, "Provisioning"))).toBe(orphan);
    expect(receiveRow(orphan, rowAt(7))["bot-one"].left).toBeNull();
  });
  it("discard removes the entry and so ends the reload warning", () => {
    const orphan = receiveRow({ "bot-one": edit(newEntry(row), { name: "Mine" }) }, rowAt(6, {}, "Provisioning"));
    expect(without(orphan, "bot-one")).toEqual({});
  });
});

describe("own-write match compares the whole draft", () => {
  const budget = (patch: object) => ({ ...draft.budget!, ...patch });
  const sent = begin(edit(newEntry(row), { name: "Mine" }), "save", { ...draft, name: "Mine" });
  for (const [label, b] of [["currency", budget({ currency: "EUR" })], ["period", budget({ period: "day" })], ["exponent", budget({ exponent: 3 })], ["presence", null]] as const) {
    it(`a row with a different budget ${label} is another client's write`, () => {
      expect(receive(edit(sent, { instructions: "Z" }).editor, rowAt(5, { name: "Mine", budget: b })).conflict?.version).toBe(5);
    });
  }
});

it("allows explicit conflict resolution after a sent write meets a different revision", () => {
  const entered = edit(newEntry(row), { name: "Mine" });
  const sent = begin(entered, "save", validate(entered.editor.fields, row.setup.draft, false).draft!);
  const waiting = applyResult(sent, { kind: "saved", row: null }, row, row.setup.draft);
  const received = receiveRow({ "bot-one": waiting }, rowAt(5, { name: "Other writer" }))["bot-one"];
  expect(received.editor.conflict).not.toBeNull();
  expect(awaitingRead(received)).toBe(false);
  expect(keepMine(received).editor.base.version).toBe(5);
});
