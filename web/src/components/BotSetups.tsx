import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import { parseMinor } from "@/lib/bot";
import { WaitPanel } from "@/components/SetupWait";

const fail = (e: unknown) => toast.error((e as Error).message);
const field = "bg-background w-full rounded border px-2 py-1.5 text-sm";

function Review({ row, refresh }: { row: api.SetupRecord; refresh: () => Promise<void> }) {
  const [draft, setDraft] = useState<api.SetupDraft>(() => structuredClone(row.setup.draft));
  const [busy, setBusy] = useState(false);
  const budget = draft.budget;
  const [allowance, setAllowance] = useState(budget ? (budget.allowance / 10 ** budget.exponent).toFixed(budget.exponent) : "");
  const [ceiling, setCeiling] = useState(budget ? (budget.ceiling / 10 ** budget.exponent).toFixed(budget.exponent) : "");
  const save = async (approve: boolean) => {
    if (!budget) return toast.error("A reviewed spending budget is required.");
    const amount = (s: string) => /^0(?:\.0+)?$/.test(s.trim()) ? 0 : parseMinor(s, budget.exponent);
    const a = amount(allowance), c = amount(ceiling);
    if (a === null || c === null || a > c) return toast.error("Enter a working allowance at or below the spending ceiling.");
    setBusy(true);
    try {
      await api.editSetup(row.setup.id, row.version, { ...draft, budget: { ...budget, allowance: a, ceiling: c } });
      if (approve) await api.approveSetup(row.setup.id, row.version + 1, row.setup.draft.revision + 1);
      await refresh();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  return <div className="flex flex-col gap-3">
    <p className="text-muted-foreground text-xs">Draft. The computer has not been created. Review this setup before approval.</p>
    <label className="text-sm">Suggested name<input className={field} maxLength={200} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
    <label className="text-sm">Job and instructions<textarea className={field} rows={3} maxLength={20000} value={draft.instructions} onChange={e => setDraft({ ...draft, instructions: e.target.value })} /></label>
    <label className="text-sm">Recommended model<input className={field} value={draft.model} onChange={e => setDraft({ ...draft, model: e.target.value })} /></label>
    <label className="text-sm">Authority<select className={field} value={draft.authority === "Full" ? "full" : "restricted"} onChange={e => setDraft({ ...draft, authority: e.target.value === "full" ? "Full" : { Restricted: [] } })}><option value="full">Full authority for this job</option><option value="restricted">Only the actions I list</option></select></label>
    <p className="text-muted-foreground text-xs">Full authority covers sending, posting, submitting and deleting within this job. The bot cannot change your absolute spending ceiling.</p>
    {draft.authority !== "Full" && <label className="text-sm">Allowed actions, one per line<textarea className={field} value={draft.authority.Restricted.join("\n")} onChange={e => setDraft({ ...draft, authority: { Restricted: e.target.value.split("\n").filter(Boolean) } })} /></label>}
    <fieldset className="grid grid-cols-3 gap-2"><legend className="mb-1 text-sm">Computer resources</legend>
      {([["cpus", "CPUs"], ["memory_mib", "Memory (MiB)"], ["disk_gib", "Disk (GiB)"]] as const).map(([key, label]) => <label key={key} className="text-xs">{label}<input type="number" min={1} className={field} value={draft.resources[key]} onChange={e => setDraft({ ...draft, resources: { ...draft.resources, [key]: Number(e.target.value) } })} /></label>)}
    </fieldset>
    <p className="text-muted-foreground text-xs">The current computer image supports the recommended resource settings. Unsupported changes need a supported image.</p>
    {budget && <fieldset className="grid grid-cols-2 gap-2"><legend className="mb-1 text-sm">Spending in {budget.currency}</legend>
      <label className="text-xs">Initial working allowance<input inputMode="decimal" className={field} value={allowance} onChange={e => setAllowance(e.target.value)} /></label>
      <label className="text-xs">Absolute ceiling<input inputMode="decimal" className={field} value={ceiling} onChange={e => setCeiling(e.target.value)} /></label>
      <label className="col-span-2 text-xs">Accounting period<input className={field} value={budget.period} onChange={e => setDraft({ ...draft, budget: { ...budget, period: e.target.value } })} /></label>
    </fieldset>}
    <label className="text-sm">Credential labels, one per line<textarea className={field} placeholder="Labels only. Never enter passwords here." value={draft.credentials.join("\n")} onChange={e => setDraft({ ...draft, credentials: e.target.value.split("\n").map(s => s.trim()).filter(Boolean) })} /></label>
    <p className="text-muted-foreground text-xs">A bot that needs credentials asks for them through protected input after setup. It preserves progress and waits. If protected input is unavailable, it stays blocked.</p>
    <label className="text-sm">First task, if you have one<textarea className={field} value={draft.task ?? ""} onChange={e => setDraft({ ...draft, task: e.target.value.trim() ? e.target.value : null })} /></label>
    <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={busy} onClick={() => save(false)}>Save draft</Button><Button disabled={busy || !budget} onClick={() => save(true)}>Approve setup and create computer</Button></div>
  </div>;
}

export function BotSetups({ onOpen }: { onOpen: (id: string, chat: number) => void }) {
  const [enabled, setEnabled] = useState(false);
  const [rows, setRows] = useState<api.SetupRecord[]>([]);
  const [job, setJob] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [protectedInput, setProtectedInput] = useState(false);
  const refresh = async () => { const [r, o] = await Promise.all([api.listSetups(), api.setupOptions()]); setRows(r); setProtectedInput(o.protected_input === true); setError(null); };
  useEffect(() => { let closed = false; Promise.all([api.setupOptions(), api.listSetups()]).then(([o, r]) => { if (!closed) { setEnabled(o.enabled); setProtectedInput(o.protected_input === true); setRows(r); } }, e => { if (!closed) setError((e as Error).message); }); return () => { closed = true; }; }, []);
  useEffect(() => {
    if (!rows.some(r => r.phase === "Provisioning" || r.phase === "Waiting" || (r.phase === "Ready" && !r.runtime_ready))) return;
    const timer = setTimeout(() => refresh().catch(e => setError((e as Error).message)), 2000);
    return () => clearTimeout(timer);
  }, [rows]);
  if (!enabled && rows.length === 0) return null;
  const create = async () => { setBusy(true); try { const r = await api.createSetup(job); setJob(""); setEditing(r.setup.id); await refresh(); } catch (e) { fail(e); } finally { setBusy(false); } };
  const control = async (r: api.SetupRecord, command: unknown) => { try { await api.controlSetup(r.setup.id, r.version, command); await refresh(); } catch (e) { fail(e); } };
  return <section aria-label="Bot setup" className="mx-auto flex w-full max-w-2xl flex-col gap-4 p-4">
    <div className="flex items-center justify-between"><h2 className="text-sm font-medium">Your bots</h2><Button size="sm" variant="ghost" onClick={() => refresh().catch(fail)}>Refresh</Button></div>
    {enabled && <form className="flex flex-col gap-2" onSubmit={e => { e.preventDefault(); if (job.trim()) create(); }}><label className="text-sm">Describe the new bot’s job<textarea className={field} rows={2} maxLength={20000} value={job} onChange={e => setJob(e.target.value)} placeholder="What should this bot do? Do not include credentials." /></label><Button className="self-start" type="submit" disabled={busy || !job.trim()}>Propose setup</Button></form>}
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    {rows.map(r => <article key={r.setup.id} className="bg-card flex flex-col gap-3 rounded-xl border p-4">
      <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-medium">{r.setup.draft.name}</h3><span className="text-muted-foreground text-xs">{r.runtime_ready ? "Ready" : r.phase}</span></div>
      {r.phase === "Draft" ? <><Button size="sm" variant="outline" onClick={() => setEditing(editing === r.setup.id ? null : r.setup.id)}>Review setup</Button>{editing === r.setup.id && <Review key={`${r.setup.id}-${r.version}`} row={r} refresh={refresh} />}</> : <>
        <ul className="text-sm">{r.checklist.map(i => <li key={i.label}>{i.done ? "✓" : "·"} {i.label}</li>)}</ul>
        {r.error && <p role="alert" className="text-sm text-red-500">{r.error}</p>}
        {r.setup.wait && <WaitPanel row={r} protectedInput={protectedInput} refresh={refresh} />}
        {r.phase === "Investigate" && <p className="text-sm">A setup step has an unknown outcome. Investigate before retrying.</p>}
        {r.phase === "Exhausted" && <label className="text-sm">Attempts exhausted. New total attempt limit<input type="number" min={1} defaultValue={r.setup.max_attempts} className={field} onBlur={e => { const value = Number(e.target.value); if (Number.isInteger(value) && value > r.setup.max_attempts) control(r, { action: "attempts", value }); }} /></label>}
        {r.runtime_ready ? <Button onClick={() => onOpen(r.setup.id, r.chat_id)}>Open bot chat</Button> : <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => control(r, { action: "retry" })}>Refresh setup progress</Button><Button size="sm" variant="outline" onClick={() => control(r, { action: "pause", value: true })}>Pause setup</Button><Button size="sm" variant="outline" onClick={() => control(r, { action: "pause", value: false })}>Resume setup</Button></div>}
      </>}
    </article>)}
  </section>;
}
