import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import { deliverSecret, describeTaskSecret, describeWait, waitFor } from "@/lib/wait";

const field = "bg-background w-full rounded border px-2 py-1.5 text-sm";
const now = () => Math.floor(Date.now() / 1000);

/** One password-style field. Its value lives only in the DOM node and is cleared on submit, failure and unmount. */
function SecretForm({ id, label, send, done, onDone }: { id: string; label: string; send: (value: string) => Promise<void>; done: string; onDone: () => Promise<void> }) {
  const input = useRef<HTMLInputElement>(null);
  const guard = useRef({ busy: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clear = () => { if (input.current) input.current.value = ""; };
  useEffect(() => { const node = input.current; return () => { if (node) node.value = ""; }; }, []);
  const submit = async () => {
    setBusy(true);
    const r = await deliverSecret({ read: () => input.current?.value ?? "", clear, guard: guard.current, send });
    setBusy(false);
    if (r.ok) { setError(null); toast.success(done); await onDone().catch(() => {}); }
    else { setError(r.message); await onDone().catch(() => {}); }
  };
  return <form className="flex flex-col gap-2" onSubmit={e => { e.preventDefault(); if (!busy) submit(); }} onKeyDown={e => { if (e.key === "Escape") clear(); }}>
    <label className="text-sm">{label}
      <input ref={input} className={field} type="password" maxLength={4096} name="deck-protected-value" autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} data-1p-ignore data-lpignore="true" disabled={busy} aria-invalid={error !== null} aria-describedby={`${id}-note`} />
    </label>
    <p id={`${id}-note`} className="text-muted-foreground text-xs">Sent only to the protected endpoint. Never put it in chat.</p>
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    <div className="flex gap-2"><Button size="sm" type="submit" disabled={busy}>{busy ? "Delivering…" : "Deliver securely"}</Button><Button size="sm" type="button" variant="outline" disabled={busy} onClick={() => { clear(); setError(null); }}>Clear</Button></div>
  </form>;
}

/** The pending owner request of one setup. It always posts to that setup's own route. */
export function WaitPanel({ row, protectedInput, refresh }: { row: api.SetupRecord; protectedInput: boolean; refresh: () => Promise<void> }) {
  const wait = row.setup.wait;
  const [tick, setTick] = useState(now());
  const [busy, setBusy] = useState(false);
  useEffect(() => { const t = setInterval(() => setTick(now()), 1000); return () => clearInterval(t); }, []);
  if (!wait) return null;
  const view = describeWait(wait, protectedInput, tick);
  const label = typeof wait.kind === "object" && "Credential" in wait.kind ? wait.kind.Credential.label : "";
  const answer = async (decision: api.ChallengeDecision) => {
    setBusy(true);
    try { await api.respondChallenge(row.setup.id, row.version, wait.request_id, decision); await refresh(); }
    catch (e) { toast.error((e as Error).message); await refresh().catch(() => {}); }
    finally { setBusy(false); }
  };
  const left = wait.expires_at != null ? Math.max(0, wait.expires_at - tick) : null;
  return <section aria-label={`Setup request for ${row.setup.draft.name}`} className="bg-muted/40 flex flex-col gap-2 rounded-lg border p-3">
    <p role="status" className="text-sm font-medium">{view.title}</p>
    <p className="text-sm">{view.detail}</p>
    {left !== null && view.mode !== "expired" && view.mode !== "pending" && <p className="text-muted-foreground text-xs">Expires in {Math.floor(left / 60)}m {left % 60}s.</p>}
    {/* Keyed by request, so a replacement request starts with an empty field. */}
    {view.mode === "secret" && <SecretForm key={wait.request_id} id={wait.request_id} label={label} done="Delivered. Setup is continuing." send={v => api.submitSecret(row.setup.id, row.version, wait.request_id, v)} onDone={refresh} />}
    {view.mode === "challenge" && <div className="flex gap-2"><Button size="sm" disabled={busy} onClick={() => answer("completed")}>I completed it</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => answer("cancel")}>Cancel this request</Button></div>}
    {(view.mode === "expired" || view.mode === "pending") && <Button size="sm" variant="outline" className="self-start" onClick={() => refresh().catch(e => toast.error((e as Error).message))}>Refresh</Button>}
  </section>;
}

/** A protected-input request of the bot's active task. It posts to the bot's own task route, never to a setup route or to chat. */
export function TaskSecretPanel({ botId, request, refresh }: { botId: string; request: api.TaskSecretRequest; refresh: () => Promise<void> }) {
  const [tick, setTick] = useState(now());
  useEffect(() => { const t = setInterval(() => setTick(now()), 1000); return () => clearInterval(t); }, []);
  const view = describeTaskSecret(request, tick);
  const left = Math.max(0, request.expires_at - tick);
  return <section aria-label={`Protected input for ${request.label}`} className="bg-muted/40 flex flex-col gap-2 rounded-lg border p-3">
    <p role="status" className="text-sm font-medium">{view.title}</p>
    <p className="text-sm">{view.detail}</p>
    {view.mode === "secret" && <p className="text-muted-foreground text-xs">Expires in {Math.floor(left / 60)}m {left % 60}s.</p>}
    {/* Keyed by request, so a replacement request starts with an empty field. */}
    {view.mode === "secret" && <SecretForm key={request.request_id} id={request.request_id} label={request.label} done="Delivered. The task is continuing." send={v => api.submitTaskSecret(botId, request.request_id, v)} onDone={refresh} />}
    {view.mode === "expired" && <Button size="sm" variant="outline" className="self-start" onClick={() => refresh().catch(e => toast.error((e as Error).message))}>Refresh</Button>}
  </section>;
}

/** Shows the active bot's own pending requests in its chat: setup waits and active-task prompts. */
export function ChatSetupWait({ botId }: { botId: string | undefined }) {
  const [rows, setRows] = useState<api.SetupRecord[]>([]);
  const [tasks, setTasks] = useState<api.TaskSecretRequest[]>([]);
  const [pi, setPi] = useState(false);
  const refresh = async () => {
    const [r, o, t] = await Promise.all([api.listSetups(), api.setupOptions(), botId ? api.listSecretRequests(botId).catch(() => []) : Promise.resolve([])]);
    setRows(r); setPi(o.protected_input === true); setTasks(t);
  };
  useEffect(() => {
    if (!botId) return;
    let closed = false;
    const run = () => refresh().catch(() => {}).finally(() => { if (!closed) timer = setTimeout(run, 3000); });
    let timer = setTimeout(run, 0);
    return () => { closed = true; clearTimeout(timer); setRows([]); setTasks([]); };
  }, [botId]);
  const row = waitFor(rows, botId);
  const task = botId ? tasks[0] : undefined;
  return row || task ? <div className="flex flex-col gap-2 px-4 py-2">
    {row && <WaitPanel row={row} protectedInput={pi} refresh={refresh} />}
    {task && botId && <TaskSecretPanel botId={botId} request={task} refresh={refresh} />}
  </div> : null;
}
