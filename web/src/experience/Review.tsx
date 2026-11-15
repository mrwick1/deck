import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import type { SetupRecord } from "../lib/api";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import { reconcile, submitReview } from "./actions";
import { applyResult, awaitingRead, begin, edit, isDirty, keepMine, reset, type Entry } from "./setup-editor";
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
  const awaiting = awaitingRead(entry), gone = row.phase !== "Draft" || !!entry.left;
  const blocked = busy || disabled || !!conflict || !!unconfirmed || awaiting || gone;
  const lastField = useRef<HTMLElement | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  useEffect(() => {
    if (!focus) return;
    const target = document.getElementById(`review-${focus.key}`);
    target?.focus(); target?.scrollIntoView({ block: "nearest" });
  }, [focus]);
  useEffect(() => { if (confirming) { const keep = document.querySelector<HTMLElement>(".review-confirm-keep"); keep?.focus({ preventScroll: true }); keep?.closest(".review-confirm")?.scrollIntoView({ block: "nearest" }); } }, [confirming]);
  // A disabled fieldset drops focus from a field. Put it back when the lock ends.
  useEffect(() => {
    if (!locked && lastField.current?.isConnected && document.activeElement === document.body) lastField.current.focus({ preventScroll: true });
    if (!locked) lastField.current = null;
  }, [locked]);

  /** The button that was clicked leaves the page. Hand focus to a control that stays. */
  const afterRender = (selector: string, when: () => boolean = () => true) => requestAnimationFrame(() => { if (when()) document.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true }); });
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
    if (!unconfirmed || guard.has(id) || busy || disabled || gone) return;
    const onCheck = document.activeElement?.classList.contains("review-check") ?? false;
    guard.add(id);
    patch(id, current => ({ ...current, phase: "checking" }));
    try {
      const result = await reconcile(unconfirmed.base, unconfirmed.submitted, unconfirmed.stage, unconfirmed.saved);
      patch(id, current => applyResult(current, result, unconfirmed.base, unconfirmed.submitted));
    } finally { guard.delete(id); }
    void refresh();
    // The Check button leaves the page once the state settles. Keep focus on a stable control, but only for an owner who stayed here.
    if (onCheck && mounted.current) afterRender(".review-save", () => mounted.current && document.activeElement === document.body);
  };
  const discard = () => {
    if (busy || unconfirmed || awaiting) return;
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
  const hold = conflict ? "Resolve the conflict above before saving." : awaiting && !unconfirmed ? "Waiting for the server to confirm your last save. You can keep typing. Save and Approve return once it is confirmed." : disabled ? "Deck is not connected. Your edits are kept here. Save and Approve return with the connection." : "";

  return <form className="review-form" noValidate aria-busy={locked} onSubmit={event => { event.preventDefault(); void run(false); }}>
    <div className="review-meta">
      <p className="fine-print">Review before creating the computer. Edits stay in this app until you save.</p>
      <p className="review-dirty">{unconfirmed ? "Save not confirmed" : dirty ? awaiting ? "Edits since the last save are not saved" : "Unsaved edits" : awaiting ? "Saved. Waiting for the server to confirm" : "No unsaved edits"}</p>
      {confirming
        ? <div className="review-confirm" role="group" aria-label="Discard unsaved edits"><span>Discard your edits?</span><Button type="button" variant="outline" onClick={discard}>Discard</Button><Button type="button" variant="outline" className="review-confirm-keep" onClick={() => { setConfirming(false); afterRender(".review-reload"); }}>Keep editing</Button></div>
        : <Button type="button" variant="ghost" className="review-reload" aria-disabled={busy || !!unconfirmed || awaiting} onClick={discard}>Reload saved draft</Button>}
    </div>
    {conflict && <section className="review-conflict" role="alert" aria-label="Changed elsewhere">
      <p><strong>This setup changed elsewhere.</strong> Your edits are kept. Nothing was overwritten or approved.</p>
      <div className="review-tools"><Button type="button" variant="outline" aria-disabled={busy || !!unconfirmed || awaiting} onClick={discard}>Discard mine, load latest</Button><Button type="button" variant="outline" aria-disabled={busy || !!unconfirmed} onClick={() => { if (busy || unconfirmed || awaiting || gone) return; patch(id, keepMine); afterRender("#review-name"); }}>Keep mine, replace latest</Button></div>
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
        {awaiting && !unconfirmed && <Button type="button" variant="outline" onClick={() => { void refresh(); }}>Refresh saved state</Button>}
        {unconfirmed && <Button type="button" variant="outline" className="review-check" aria-disabled={busy || disabled} onClick={() => { void check(); }}>Check saved state</Button>}
      </div>
      <div className="review-actions">
        <Button type="submit" variant="outline" className="review-save" aria-disabled={blocked}>{saving ? "Saving draft…" : phase === "checking" && entry.action === "save" ? "Checking…" : "Save draft"}</Button>
        <Button type="button" className="review-approve" aria-disabled={blocked || !budget} onClick={() => { void run(true); }}>{approving ? phase === "approving" ? "Approving…" : phase === "checking" ? "Checking…" : "Saving draft…" : "Approve and create"}</Button>
      </div>
    </div>
  </form>;
}

const RECOVERY: [keyof Fields, string, boolean][] = [["name", "Name", false], ["instructions", "Job and instructions", true], ["allowed", "Allowed actions", true], ["allowance", "Working allowance", false], ["ceiling", "Absolute ceiling", false], ["model", "Model", false], ["cpus", "CPUs", false], ["memory_mib", "Memory (MiB)", false], ["disk_gib", "Disk (GiB)", false], ["credentials", "Credential labels", true], ["task", "First task", true]];
const PHASE_TEXT: Record<SetupRecord["phase"], string> = { Draft: "a draft", Provisioning: "being prepared", Waiting: "waiting for you", Investigate: "waiting for investigation", Exhausted: "out of setup attempts", Ready: "ready" };

/**
 * Read-only recovery for edits that were never saved when the setup moved past Draft elsewhere.
 * It has no Save, Approve or Keep mine. The owner can copy the text and then discard it on purpose.
 */
export function Recovery({ entry, discard }: { entry: Entry; discard: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [copied, setCopied] = useState("");
  const { fields } = entry.editor, left = entry.left, uncertain = !!entry.unconfirmed;
  const shown = RECOVERY.filter(([key]) => !(key === "allowed" && !fields.restricted) && !((key === "allowance" || key === "ceiling") && !entry.editor.base.setup.draft.budget));
  const asText = () => [...shown.map(([key, label]) => `${label}:\n${fields[key]}`), `Authority: ${fields.restricted ? "Only listed actions" : "Full authority"}`].join("\n\n");
  useEffect(() => { if (confirming) { const keep = document.querySelector<HTMLElement>(".recovery-keep"); keep?.focus({ preventScroll: true }); keep?.closest(".review-confirm")?.scrollIntoView({ block: "nearest" }); } }, [confirming]);
  const copy = async () => {
    try { await navigator.clipboard.writeText(asText()); setCopied("Copied your edits."); }
    catch { setCopied("Copy was not allowed. Select the text in the boxes and copy it."); }
  };
  return <section className="review-recovery" aria-labelledby="recovery-title">
    <h3 id="recovery-title" tabIndex={-1}>Your local draft is kept here</h3>
    <p>This setup moved on elsewhere{left ? ` and is now ${PHASE_TEXT[left.phase]}` : ""}. It can no longer be edited here, so Save and Approve are off. {uncertain ? "A write from this screen was never confirmed, so these edits may or may not be on the server. " : "Changes made after your last confirmed save were not submitted from this screen. "}The text below is kept so you can copy it. See the setup progress below.</p>
    <div className="review-recovery-fields">
      {shown.map(([key, label, long]) => <div className="review-field" key={key}><label htmlFor={`recovery-${key}`}>{label}</label>{long ? <Textarea id={`recovery-${key}`} readOnly rows={Math.min(6, Math.max(2, fields[key].toString().split("\n").length))} value={fields[key].toString()}/> : <Input id={`recovery-${key}`} readOnly value={fields[key].toString()}/>}</div>)}
      <p className="fine-print">Authority: {fields.restricted ? "only the listed actions" : "full authority for the job"}.</p>
    </div>
    <p role="status" className="review-success">{copied}</p>
    {confirming
      ? <div className="review-confirm" role="group" aria-label="Discard unsaved edits"><span>Discard these edits for good?</span><Button type="button" variant="outline" onClick={discard}>Discard edits</Button><Button type="button" variant="outline" className="recovery-keep" onClick={() => { setConfirming(false); requestAnimationFrame(() => document.querySelector<HTMLElement>(".recovery-discard")?.focus({ preventScroll: true })); }}>Keep them</Button></div>
      : <div className="review-tools"><Button type="button" variant="outline" onClick={() => { void copy(); }}>Copy my edits</Button><Button type="button" variant="outline" className="recovery-discard" onClick={() => setConfirming(true)}>Discard my edits</Button></div>}
  </section>;
}
