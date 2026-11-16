import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SetupDraft, SetupRecord } from "../lib/api";
import { Review } from "./Review";
import { edit, newEntry, receiveRow, type Entry } from "./setup-editor";

const draft: SetupDraft = { revision: 3, name: "Mail", instructions: "Read mail", model: "configured", authority: "Full", resources: { cpus: 2, memory_mib: 1024, disk_gib: 8 }, budget: { currency: "USD", exponent: 2, period: "month", allowance: 0, ceiling: 0 }, credentials: [], task: null };
const rowWith = (patch: Partial<SetupDraft> = {}, version = 4): SetupRecord => ({ version, chat_id: 1, phase: "Draft", runtime_ready: false, error: null, checklist: [], setup: { id: "bot-one", draft: { ...draft, ...patch, revision: version - 1 }, max_attempts: 3, wait: null, steps: [] } });
const html = (row = rowWith(), change: (entry: Entry) => Entry = entry => entry, disabled = false) =>
  renderToStaticMarkup(createElement(Review, { row, entry: change(newEntry(row)), patch: () => {}, guard: new Set<string>(), disabled, refresh: async () => {} }));
const buttons = (markup: string) => [...markup.matchAll(/<button\b([^>]*)>(.*?)<\/button>/g)].map(m => ({ attrs: m[1], text: m[2].replace(/<[^>]+>/g, "") }));
const button = (markup: string, text: RegExp) => buttons(markup).find(b => text.test(b.text))!;

describe("review form markup", () => {
  it("makes only Save draft a submit button, so Enter never approves", () => {
    const list = buttons(html()), submits = list.filter(b => /type="submit"/.test(b.attrs));
    expect(submits.map(b => b.text)).toEqual(["Save draft"]);
    expect(button(html(), /Approve/).attrs).toContain('type="button"');
    expect(button(html(), /Reload saved draft/).attrs).toContain('type="button"');
  });
  it("shows budget amounts exactly at the largest safe integer", () => {
    const budget = (exponent: number) => ({ currency: "USD", exponent, period: "month", allowance: 0, ceiling: Number.MAX_SAFE_INTEGER });
    expect(html(rowWith({ budget: budget(3) }))).toContain('value="9007199254740.991"');
    expect(html(rowWith({ budget: budget(6) }))).toContain('value="9007199254.740991"');
  });
  it("renders the unsaved text the owner typed, not the server text", () => {
    const markup = html(rowWith(), entry => edit(entry, { name: "Typed, unsaved" }));
    expect(markup).toContain('value="Typed, unsaved"');
    expect(markup).toContain("Unsaved edits");
    expect(html()).toContain("No unsaved edits");
  });
  it("keeps typed text, and flags a conflict, when a newer row arrives during edits", () => {
    const row = rowWith(), entries = receiveRow({ "bot-one": edit(newEntry(row), { name: "Typed, unsaved" }) }, rowWith({ name: "Theirs" }, 5));
    const markup = renderToStaticMarkup(createElement(Review, { row: rowWith({ name: "Theirs" }, 5), entry: entries["bot-one"], patch: () => {}, guard: new Set<string>(), disabled: false, refresh: async () => {} }));
    expect(markup).toContain('value="Typed, unsaved"');
    expect(markup).not.toContain('value="Theirs"');
    expect(markup).toContain("changed elsewhere");
    expect(button(markup, /Discard mine/)).toBeTruthy();
    expect(button(markup, /Save draft/).attrs).toContain('aria-disabled="true"');
    expect(button(markup, /Approve/).attrs).toContain('aria-disabled="true"');
  });
  it("locks the fields while saving, labels the real phase and keeps both actions focusable", () => {
    const markup = html(rowWith(), entry => ({ ...entry, phase: "saving", action: "save" }));
    expect(markup).toMatch(/<fieldset class="review-fields" disabled=""/);
    expect(markup).toContain('aria-busy="true"');
    expect(button(markup, /Saving draft/).attrs).toContain('type="submit"');
    expect(button(markup, /Approve and create/).attrs).toContain('aria-disabled="true"');
    for (const b of buttons(markup)) expect(b.attrs).not.toMatch(/\sdisabled(=|\s|$)/);
  });
  it("labels Approve as approving, and Save untouched, only once approval starts", () => {
    const saving = html(rowWith(), entry => ({ ...entry, phase: "saving", action: "approve" }));
    expect(button(saving, /Saving draft/).attrs).toContain('type="button"');
    expect(saving).not.toContain("Approving");
    const approving = html(rowWith(), entry => ({ ...entry, phase: "approving", action: "approve" }));
    expect(button(approving, /Approving/).attrs).toContain('type="button"');
    expect(button(approving, /Save draft/).attrs).toContain('aria-disabled="true"');
    expect(approving).toContain("Approving…");
  });
  it("is unlocked and not busy at rest", () => {
    const markup = html();
    expect(markup).not.toMatch(/fieldset class="review-fields" disabled/);
    expect(markup).toContain('aria-busy="false"');
    expect(button(markup, /Save draft/).attrs).toContain('aria-disabled="false"');
  });
  it("announces success and errors in reserved live regions, and nothing else as success", () => {
    const idle = html(), ok = html(rowWith(), entry => ({ ...entry, notice: { kind: "success", text: "Draft saved." } })), bad = html(rowWith(), entry => ({ ...entry, notice: { kind: "error", text: "Not saved: limits" } }));
    expect(idle).toContain('<p role="status" class="review-success"></p>');
    expect(idle).toContain('<p role="alert" class="review-error"></p>');
    expect(ok).toContain('<p role="status" class="review-success">Draft saved.</p>');
    expect(bad).toContain('<p role="alert" class="review-error">Not saved: limits</p>');
    expect(html(rowWith(), entry => ({ ...entry, phase: "saving" }))).toMatch(/role="status"[^>]*>Saving/);
  });
  it("blocks submit and offers a check after an unconfirmed write", () => {
    const row = rowWith(), markup = html(row, entry => ({ ...entry, unconfirmed: { stage: "save", base: row, submitted: row.setup.draft, saved: null }, notice: { kind: "error", text: "Deck could not confirm" } }));
    expect(button(markup, /Check saved state/)).toBeTruthy();
    expect(button(markup, /Save draft/).attrs).toContain('aria-disabled="true"');
    expect(button(markup, /Approve/).attrs).toContain('aria-disabled="true"');
  });
  it("explains a stale connection and keeps the draft", () => {
    const markup = html(rowWith(), entry => edit(entry, { name: "Kept" }), true);
    expect(markup).toContain("Your edits are kept here");
    expect(markup).toContain('value="Kept"');
    expect(button(markup, /Save draft/).attrs).toContain('aria-disabled="true"');
  });
  it("blocks approval and explains it when the server has no budget", () => {
    const markup = html(rowWith({ budget: null }));
    expect(markup).toContain("Configure a priced model and spending budget");
    expect(button(markup, /Approve/).attrs).toContain('aria-disabled="true"');
    expect(button(markup, /Save draft/).attrs).toContain('aria-disabled="false"');
  });
  it("explains zero spending and an empty restricted list without rejecting them", () => {
    const zero = html(rowWith({ authority: { Restricted: [] } }));
    expect(zero).toContain("A ceiling of 0 allows no spending.");
    expect(zero).toContain("No actions are listed. The bot can take none.");
    expect(button(zero, /Approve/).attrs).toContain('aria-disabled="false"');
    expect(html(rowWith({ budget: { currency: "USD", exponent: 2, period: "month", allowance: 0, ceiling: 500 } }))).toContain("An allowance of 0 allows no spending until you raise it.");
  });
  it("gives credential labels a persistent hint and protects the field from autofill", () => {
    const markup = html();
    expect(markup).toMatch(/id="review-credentials-hint"[^>]*>Labels only, never passwords/);
    expect(markup).toMatch(/<textarea[^>]*id="review-credentials"[^>]*aria-describedby="review-credentials-hint"/);
    for (const key of ["credentials", "model", "name"]) expect(markup).toMatch(new RegExp(`id="review-${key}"[^>]*autoComplete="off"|autoComplete="off"[^>]*id="review-${key}"`));
    expect(markup).toMatch(/spellCheck="false"/);
  });
  it("bounds text and uses 16px-safe inputs through the installed controls", () => {
    const markup = html();
    expect(markup).toContain('data-slot="input"');
    expect(markup).toContain('data-slot="textarea"');
    expect(markup).toMatch(/id="review-instructions"[^>]*maxLength="20000"|maxLength="20000"[^>]*id="review-instructions"/);
    expect(markup).toMatch(/<select id="review-restricted"/);
  });
});
