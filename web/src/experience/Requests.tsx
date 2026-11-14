import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";
import { ceilingAnswer } from "../lib/bot";
import { WaitPanel, TaskSecretPanel } from "../components/SetupWait";

export function useRequests(id: string) {
  const [items, setItems] = useState<api.InboxItem[]>([]);
  const [secrets, setSecrets] = useState<api.TaskSecretRequest[]>([]);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const alive = useRef(false);
  const pending = useRef<Promise<void> | null>(null);
  const refresh = useCallback(() => {
    if (pending.current) return pending.current;
    const request = (async () => {
      try {
        const [inbox, prompts] = await Promise.all([api.getInbox(id), api.listSecretRequests(id)]);
        if (alive.current) { setItems(inbox); setSecrets(prompts); setError(""); setLoaded(true); }
      } catch { if (alive.current) setError("Requests could not be refreshed. Retry before responding."); }
      finally { pending.current = null; }
    })();
    pending.current = request; return request;
  }, [id]);
  useEffect(() => {
    alive.current = true; let closed = false; let timer: ReturnType<typeof setTimeout>;
    const run = async () => { await refresh(); if (!closed) timer = setTimeout(run, 3000); };
    void run(); return () => { alive.current = false; closed = true; clearTimeout(timer); };
  }, [refresh]);
  return { items, secrets, error, loaded, refresh };
}

function Request({ item, id, refresh, disabled }: { item: api.InboxItem; id: string; refresh: () => Promise<void>; disabled: boolean }) {
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const guard = useRef(false);
  const responseKeys = useRef(new Map<string, string>());
  const run = async (action: () => Promise<unknown>) => {
    if (guard.current || disabled) return;
    guard.current = true; setBusy(true); setError("");
    try { await action(); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "The response was not confirmed."); await refresh(); }
    finally { guard.current = false; setBusy(false); }
  };
  const ceiling = (decision: "approve" | "deny") => {
    if (!item.ceiling) return;
    const signature = `${item.ceiling.id}:${item.ceiling.version}:${decision}:${amount}`;
    let key = responseKeys.current.get(signature);
    if (!key) { key = crypto.randomUUID(); responseKeys.current.set(signature, key); }
    const answer = ceilingAnswer(item.ceiling, decision, amount, key);
    if (!answer) return setError("Enter an amount above the current ceiling.");
    void run(() => api.botAnswerCeiling(answer, id));
  };
  return <article className="request-card"><h3>{item.title}</h3><p>{item.detail}</p>{error && <p role="alert" className="error-note">{error}</p>}<fieldset disabled={disabled || busy}>
    {item.kind === "ceiling_request" && item.ceiling && <><p>Current ceiling: {(item.ceiling.current_ceiling_minor / 10 ** item.ceiling.precision).toFixed(item.ceiling.precision)} {item.ceiling.currency}</p><label>New ceiling<input inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)}/></label><div className="review-actions"><button className="secondary" onClick={() => ceiling("deny")}>Keep ceiling</button><button className="primary" onClick={() => ceiling("approve")}>Raise ceiling</button></div></>}
    {item.kind === "unknown_outcome" && <div className="review-actions">{[["ran", "It ran"], ["not_run", "It did not run"], ["dismissed", "Dismiss"]].map(([decision, label]) => <button key={decision} className="secondary" onClick={() => { void run(() => api.botDecide(item.id, decision, "", id)); }}>{label}</button>)}</div>}
    {item.kind === "budget_hold" && <button className="secondary" onClick={() => { void run(() => api.botApprove(id)); }}>Release hold without changing ceiling</button>}
    {item.kind === "held_task" && <button className="secondary" onClick={() => { void run(() => api.botRetryTask(item.id, id)); }}>Retry task</button>}
    {(item.kind === "waiting_task" || item.kind === "held_task") && <button className="secondary" onClick={() => { void run(() => api.botCancelTask(item.task_id ?? item.id, id)); }}>Cancel task</button>}
  </fieldset></article>;
}

export function Requests({ id, row, protectedInput, requests, disabled, refresh }: {
  id: string; row?: api.SetupRecord; protectedInput: boolean; requests: ReturnType<typeof useRequests>;
  disabled: boolean; refresh: () => Promise<void>;
}) {
  const reload = async () => { await requests.refresh(); await refresh(); };
  return <div className="requests-view">
    {!requests.loaded && !requests.error && <p role="status">Loading requests…</p>}
    {requests.error && <p className="error-note" role="alert">{requests.error}<button className="text-button" onClick={() => { void reload(); }}>Refresh requests</button></p>}
    <fieldset disabled={disabled || !!requests.error}>
      {row?.setup.wait && <WaitPanel key={row.setup.wait.request_id} row={row} protectedInput={protectedInput} refresh={reload}/>}
      {requests.secrets.map(request => <TaskSecretPanel key={request.request_id} botId={id} request={request} refresh={reload}/>)}
      {requests.items.map(item => <Request key={`${item.id}-${item.ceiling?.version ?? ""}`} item={item} id={id} refresh={reload} disabled={disabled || !!requests.error}/>)}
    </fieldset>
    {requests.loaded && !requests.error && !row?.setup.wait && !requests.secrets.length && !requests.items.length && <p className="empty-result">Nothing needs your attention</p>}
  </div>;
}
