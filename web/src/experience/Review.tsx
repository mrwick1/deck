import { useRef, useState } from "react";
import type { SetupDraft, SetupRecord } from "../lib/api";
import { readAmount, saveReview } from "./actions";

export function Review({ row, disabled, refresh }: { row: SetupRecord; disabled: boolean; refresh: () => Promise<void> }) {
  const [draft, setDraft] = useState<SetupDraft>(() => structuredClone(row.setup.draft));
  const [busy, setBusy] = useState(false);
  const guard = useRef(false);
  const [error, setError] = useState("");
  const [allowed, setAllowed] = useState(draft.authority === "Full" ? "" : draft.authority.Restricted.join("\n"));
  const budget = draft.budget;
  const [allowance, setAllowance] = useState(budget ? (budget.allowance / 10 ** budget.exponent).toFixed(budget.exponent) : "");
  const [ceiling, setCeiling] = useState(budget ? (budget.ceiling / 10 ** budget.exponent).toFixed(budget.exponent) : "");
  const save = async (approve: boolean) => {
    if (guard.current || disabled) return;
    if (!draft.name.trim() || !draft.instructions.trim()) return setError("Enter a name and job instructions.");
    if (!budget && approve) return setError("The server has no configured spending budget. Setup cannot be approved.");
    const a = budget ? readAmount(allowance, budget.exponent) : 0, c = budget ? readAmount(ceiling, budget.exponent) : 0;
    if (a === null || c === null || a > c) return setError("Enter a working allowance at or below the ceiling, in the configured currency precision.");
    guard.current = true; setBusy(true); setError("");
    try { await saveReview(row, { ...draft, authority: draft.authority === "Full" ? "Full" : { Restricted: allowed.split("\n").map(value => value.trim()).filter(Boolean) }, credentials: draft.credentials.map(value => value.trim()).filter(Boolean), budget: budget ? { ...budget, allowance: a, ceiling: c } : null }, approve); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Setup was not confirmed. Refresh before trying again."); }
    finally { guard.current = false; setBusy(false); }
  };
  return <form className="review-form" onSubmit={e => { e.preventDefault(); void save(true); }}>
    <p className="fine-print">Review before creating the computer. This draft is saved on Deck.</p>
    <label>Name<input value={draft.name} maxLength={200} onChange={e => setDraft({ ...draft, name: e.target.value })}/></label>
    <label>Job and instructions<textarea rows={3} maxLength={20000} value={draft.instructions} onChange={e => setDraft({ ...draft, instructions: e.target.value })}/></label>
    <label>Authority<select value={draft.authority === "Full" ? "full" : "restricted"} onChange={e => setDraft({ ...draft, authority: e.target.value === "full" ? "Full" : { Restricted: [] } })}><option value="full">Full authority for this job</option><option value="restricted">Only listed actions</option></select></label>
    <p className="fine-print">Full authority includes sending, posting and deleting within this job. It cannot change your spending ceiling.</p>
    {draft.authority !== "Full" && <label>Allowed actions, one per line<textarea value={allowed} onChange={e => setAllowed(e.target.value)}/></label>}
    {budget ? <fieldset className="budget-fields"><legend>Spending · {budget.currency}</legend><label>Working allowance<input inputMode="decimal" value={allowance} onChange={e => setAllowance(e.target.value)}/></label><label>Absolute ceiling<input inputMode="decimal" value={ceiling} onChange={e => setCeiling(e.target.value)}/></label><p className="fine-print">Accounting period: {budget.period}. The allowance can increase only within your ceiling.</p></fieldset> : <p role="alert">Configure a priced model and spending budget on the server before approval.</p>}
    <details className="advanced"><summary>Model, computer and sign-in requirements</summary><label>Model<input value={draft.model} onChange={e => setDraft({ ...draft, model: e.target.value })}/></label>
      <div className="resource-fields">{([['cpus', 'CPUs'], ['memory_mib', 'Memory (MiB)'], ['disk_gib', 'Disk (GiB)']] as const).map(([key, label]) => <label key={key}>{label}<input type="number" min={1} value={draft.resources[key]} onChange={e => setDraft({ ...draft, resources: { ...draft.resources, [key]: Number(e.target.value) } })}/></label>)}</div>
      <p className="fine-print">Only resource settings and model profiles supported by your configured server can be applied.</p>
      <label>Credential labels, one per line<textarea value={draft.credentials.join("\n")} placeholder="Labels only. Never enter passwords here." onChange={e => setDraft({ ...draft, credentials: e.target.value.split("\n") })}/></label>
      <label>First task<textarea value={draft.task ?? ""} onChange={e => setDraft({ ...draft, task: e.target.value || null })}/></label>
    </details>
    {error && <div role="alert" className="error-note">{error}<button type="button" className="text-button" onClick={() => { void refresh(); }}>Reload saved draft</button></div>}
    <div className="review-actions"><button type="button" className="secondary" disabled={busy || disabled} onClick={() => { void save(false); }}>Save draft</button><button className="primary" disabled={busy || disabled || !budget}>{busy ? "Saving…" : "Approve and create"}</button></div>
  </form>;
}
