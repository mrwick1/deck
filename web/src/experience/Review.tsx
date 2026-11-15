import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import type { SetupRecord } from "../lib/api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { reconcile, submitReview } from "./actions";
import { applyResult, begin, edit, isDirty, keepMine, reset, type Entry } from "./setup-editor";
import { ADVANCED, TEXT_LIMITS, firstInvalid, readAmount, validate, type Errors, type FieldKey, type Fields } from "./setup-form";

interface Props {
  /** The latest row from the server. Used only to reload. The editor works from its own base row. */
  row: SetupRecord;
  entry: Entry;
  patch: (id: string, change: (entry: Entry) => Entry) => void;
  /** Setup ids with a write in flight. It lives in App, so it survives leaving this screen. */
  guard: Set<string>;
  disabled: boolean;
  refresh: () => Promise<void>;
}
const RESOURCES = [["cpus", "CPUs"], ["memory_mib", "Memory (MiB)"], ["disk_gib", "Disk (GiB)"]] as const;

export function Review({ row, entry, patch, guard, disabled, refresh }: Props) {
  const id = row.setup.id, { editor, phase, notice, unconfirmed } = entry, { fields, base, conflict } = editor, budget = base.setup.draft.budget;
  const [errors, setErrors] = useState<Errors>({});
  const [advanced, setAdvanced] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [focus, setFocus] = useState<{ key: FieldKey; n: number } | null>(null);
  const busy = phase !== "idle", locked = busy;
  const dirty = isDirty(editor);
  const blocked = busy || disabled || !!conflict || !!unconfirmed;
  const lastField = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!focus) return;
    const target = document.getElementById(`review-${focus.key}`);
    target?.focus(); target?.scrollIntoView({ block: "nearest" });
  }, [focus]);
  // A disabled fieldset drops focus from a field. Put it back when the lock ends.
  useEffect(() => {
    if (!locked && lastField.current?.isConnected && document.activeElement === document.body) lastField.current.focus({ preventScroll: true });
    if (!locked) lastField.current = null;
  }, [locked]);

  /** The button that was clicked leaves the page. Hand focus to a control that stays. */
  const afterRender = (selector: string) => requestAnimationFrame(() => document.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true }));
  const set = (change: Partial<Fields>) => {
    patch(id, current => edit(current, change));
    setErrors(previous => { const next = { ...previous }; for (const key of Object.keys(change)) delete next[key as FieldKey]; return next; });
  };
  const run = async (approve: boolean) => {
    if (guard.has(id) || blocked) return;
    const active = document.activeElement;
    lastField.current = active instanceof HTMLElement && active.closest(".review-fields") ? active : null;
    const { errors: found, draft } = validate(fields, base.setup.draft, approve);
    setErrors(found);
    if (!draft) {
      const key = firstInvalid(found);
      if (key) { if (ADVANCED.includes(key)) setAdvanced(true); setFocus(previous => ({ key, n: (previous?.n ?? 0) + 1 })); }
      return;
    }
    const submitted = base;
    guard.add(id);
    patch(id, current => begin(current, approve ? "approve" : "save", draft));
    try {
      const result = await submitReview(submitted, draft, approve, next => patch(id, current => ({ ...current, phase: next })));
      patch(id, current => applyResult(current, result, submitted, draft));
    } catch {
      patch(id, current => applyResult(current, { kind: "unconfirmed", stage: approve ? "approve" : "save", saved: null }, submitted, draft));
    } finally { guard.delete(id); }
    void refresh();
  };
  const check = async () => {
    if (!unconfirmed || guard.has(id) || busy || disabled) return;
    guard.add(id);
    patch(id, current => ({ ...current, phase: "checking" }));
    try {
      const result = await reconcile(unconfirmed.base, unconfirmed.submitted, unconfirmed.stage, unconfirmed.saved);
      patch(id, current => applyResult(current, result, unconfirmed.base, unconfirmed.submitted));
    } finally { guard.delete(id); }
    void refresh();
  };
  const discard = () => {
    if (busy || unconfirmed) return;
    if (dirty && !confirming) { setConfirming(true); return; }
    setConfirming(false); setErrors({});
    patch(id, current => reset(current, row));
    afterRender(".review-reload");
    void refresh();
  };

  const describe = (key: FieldKey, hint = false) => [hint && `review-${key}-hint`, errors[key] && `review-${key}-error`].filter(Boolean).join(" ") || undefined;
  const invalid = (key: FieldKey) => errors[key] ? true : undefined;
  const text = (key: Exclude<keyof Fields, "restricted">) => ({ id: `review-${key}`, value: fields[key], "aria-invalid": invalid(key), "aria-describedby": describe(key, key === "credentials" || key === "allowance" || key === "ceiling" || key === "allowed"), onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => set({ [key]: event.target.value }) });
  const plain = { autoComplete: "off", spellCheck: false, autoCapitalize: "off" } as const;
  const field = (key: FieldKey, label: string, control: ReactNode, hint?: ReactNode) => <div className="review-field">
    <label htmlFor={`review-${key}`}>{label}</label>{control}
    {hint && <p id={`review-${key}-hint`} className="fine-print">{hint}</p>}
    {errors[key] && <p id={`review-${key}-error`} className="field-error">{errors[key]}</p>}
  </div>;

  const allowance = budget ? readAmount(fields.allowance, budget.exponent) : null, ceiling = budget ? readAmount(fields.ceiling, budget.exponent) : null;
  const zeroNote = ceiling === 0 ? "A ceiling of 0 allows no spending." : allowance === 0 ? "An allowance of 0 allows no spending until you raise it." : "";
  const saving = phase === "saving" && entry.action === "save", approving = entry.action === "approve" && busy;
  const pending = phase === "saving" ? "Saving draft…" : phase === "approving" ? "Approving…" : phase === "checking" ? "Checking the saved state…" : "";
  const hold = pending || (conflict ? "Resolve the conflict above before saving." : disabled ? "Deck is not connected. Your edits are kept here. Save and Approve return with the connection." : "");

  return <form className="review-form" noValidate aria-busy={locked} onSubmit={event => { event.preventDefault(); void run(false); }}>
    <div className="review-meta">
      <p className="fine-print">Review before creating the computer. Edits stay in this app until you save.</p>
      <p className="review-dirty">{dirty ? "Unsaved edits" : "No unsaved edits"}</p>
      {confirming
        ? <div className="review-confirm" role="group" aria-label="Discard unsaved edits"><span>Discard your edits?</span><Button type="button" variant="outline" onClick={discard}>Discard</Button><Button type="button" variant="outline" onClick={() => { setConfirming(false); afterRender(".review-reload"); }}>Keep editing</Button></div>
        : <Button type="button" variant="ghost" className="review-reload" aria-disabled={busy || !!unconfirmed} onClick={discard}>Reload saved draft</Button>}
    </div>
    {conflict && <section className="review-conflict" role="alert" aria-label="Changed elsewhere">
      <p><strong>This setup changed elsewhere.</strong> Your edits are kept. Nothing was overwritten or approved.</p>
      <div className="review-tools"><Button type="button" variant="outline" aria-disabled={busy || !!unconfirmed} onClick={discard}>Discard mine, load latest</Button><Button type="button" variant="outline" aria-disabled={busy || !!unconfirmed} onClick={() => { if (busy || unconfirmed) return; patch(id, keepMine); afterRender("#review-name"); }}>Keep mine, replace latest</Button></div>
    </section>}
    <fieldset className="review-fields" disabled={locked}>
      {field("name", "Name", <Input {...text("name")} {...plain} maxLength={TEXT_LIMITS.name}/>)}
      {field("instructions", "Job and instructions", <Textarea {...text("instructions")} rows={4} maxLength={TEXT_LIMITS.long}/>)}
      <div className="review-field">
        <label htmlFor="review-restricted">Authority</label>
        <select id="review-restricted" aria-describedby="review-restricted-hint" value={fields.restricted ? "restricted" : "full"} onChange={event => set({ restricted: event.target.value === "restricted" })}><option value="full">Full authority for this job</option><option value="restricted">Only listed actions</option></select>
        <p id="review-restricted-hint" className="fine-print">{fields.restricted ? "Only the actions you list are allowed. Everything else is blocked." : "Full authority includes sending, posting and deleting within this job. It cannot change your spending ceiling."}</p>
      </div>
      {fields.restricted && field("allowed", "Allowed actions, one per line", <Textarea {...text("allowed")} {...plain} rows={3} maxLength={TEXT_LIMITS.long}/>, fields.allowed.trim() ? "Each line is one allowed action." : "No actions are listed. The bot can take none.")}
      {budget
        ? <fieldset className="budget-fields"><legend>Spending · {budget.currency}</legend>
          {field("allowance", "Working allowance", <Input {...text("allowance")} {...plain} inputMode="decimal"/>, `Up to ${budget.exponent} decimal places.`)}
          {field("ceiling", "Absolute ceiling", <Input {...text("ceiling")} {...plain} inputMode="decimal"/>, `Accounting period: ${budget.period}. The allowance can increase only within your ceiling.`)}
          {zeroNote && <p className="fine-print review-zero">{zeroNote}</p>}
        </fieldset>
        : <p role="alert" className="error-note">Configure a priced model and spending budget on the server before approval.</p>}
      <details className="advanced" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}><summary>Model, computer and sign-in requirements</summary>
        {field("model", "Model", <Input {...text("model")} {...plain}/>)}
        <div className="resource-fields">{RESOURCES.map(([key, label]) => <div key={key}>{field(key, label, <Input {...text(key)} {...plain} inputMode="numeric"/>)}</div>)}</div>
        <p className="fine-print">Only resource settings and model profiles supported by your configured server can be applied.</p>
        {field("credentials", "Credential labels, one per line", <Textarea {...text("credentials")} {...plain} rows={3} maxLength={TEXT_LIMITS.long}/>, "Labels only, never passwords. Deck asks you for each secret later.")}
        {field("task", "First task", <Textarea {...text("task")} rows={3} maxLength={TEXT_LIMITS.long}/>)}
      </details>
    </fieldset>
    <div className="review-footer">
      <div className="review-status">
        <p role="status" className="review-success">{pending || (notice?.kind === "success" ? notice.text : "")}</p>
        <p role="alert" className="review-error">{notice?.kind === "error" ? notice.text : ""}</p>
        {hold && <p className="review-hold">{hold}</p>}
        {unconfirmed && <Button type="button" variant="outline" className="review-check" aria-disabled={busy || disabled} onClick={() => { void check(); }}>Check saved state</Button>}
      </div>
      <div className="review-actions">
        <Button type="submit" variant="outline" className="review-save" aria-disabled={blocked}>{saving ? "Saving draft…" : phase === "checking" && entry.action === "save" ? "Checking…" : "Save draft"}</Button>
        <Button type="button" className="review-approve" aria-disabled={blocked || !budget} onClick={() => { void run(true); }}>{approving ? phase === "approving" ? "Approving…" : phase === "checking" ? "Checking…" : "Saving draft…" : "Approve and create"}</Button>
      </div>
    </div>
  </form>;
}
