import { createContext, useContext, type ReactNode, useEffect, useLayoutEffect, useRef, useSyncExternalStore } from "react";
import * as api from "../lib/api";
import { challengeResponse } from "../lib/wait";
import { WaitPanel, TaskSecretPanel } from "../components/SetupWait";
import { itemKey, money, RequestsStore, type Draft, type Op, type View } from "./requests-store";
import "./requests.css";

type Session = { store: RequestsStore; challenge: ReturnType<typeof challengeResponse> };
export type RequestSessions = Map<string, Session>;
const Sessions = createContext<RequestSessions | null>(null);

/** App-owned state survives route changes. It contains no protected values. */
export function RequestsProvider({ children, sessions }: { children: ReactNode; sessions?: RequestSessions }) {
  const own = useRef<RequestSessions>(new Map());
  return <Sessions.Provider value={sessions ?? own.current}>{children}</Sessions.Provider>;
}

export function useRequests(id: string) {
  const shared = useContext(Sessions);
  const local = useRef<RequestSessions>(new Map());
  const sessions = shared ?? local.current;
  let session = sessions.get(id);
  if (!session) {
    session = { challenge: challengeResponse(), store: new RequestsStore({ inbox: () => api.getInbox(id), secrets: () => api.listSecretRequests(id), answer: a => api.botAnswerCeiling(a, id) }) };
    sessions.set(id, session);
  }
  const { store, challenge } = session;
  const view = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => {
    let closed = false; let timer: ReturnType<typeof setTimeout>;
    const run = async () => { await store.read(); if (!closed) timer = setTimeout(run, 3000); };
    void run(); return () => { closed = true; clearTimeout(timer); };
  }, [store]);
  /** A caller asks after a write, so the read always starts after earlier reads settle. */
  const refresh = () => store.read(true).then(() => {});
  return { items: view.items, secrets: view.secrets, error: view.error, loaded: view.loaded, refresh, store, view, challenge };
}

const domId = (key: string) => `req-${key.replace(/[^\w-]/g, "_")}`;

function Request({ item, id, store, view, blocked }: { item: api.InboxItem; id: string; store: RequestsStore; view: View; blocked: boolean }) {
  const key = itemKey(item), dom = domId(key), r = item.ceiling;
  const op: Op | undefined = view.ops.get(key);
  const lock = !!op && (op.version === undefined || op.version === r?.version);
  const draft: Draft | undefined = view.drafts.get(key);
  const failure = view.failures.get(key);
  const again = view.review.has(key);
  const changed = !!r && !!draft && draft.reviewed !== r.version;
  const current = r ? money(r.current_ceiling_minor, r.precision, r.currency) : null;
  const before = r && draft ? money(draft.reviewedMinor, r.precision, r.currency) : null;
  const gated = blocked || lock || again || changed;
  const input = useRef<HTMLInputElement>(null);
  const seen = useRef(draft?.errorSeq ?? 0);
  const card = useRef<HTMLElement>(null);
  const reviewing = useRef(false);
  const review = (change: () => void) => { reviewing.current = true; change(); };
  useLayoutEffect(() => {
    if (!reviewing.current) return;
    reviewing.current = false;
    (input.current ?? card.current?.querySelector<HTMLElement>('button:not([aria-disabled="true"])'))?.focus();
  }, [view]);
  useEffect(() => { if (draft && draft.errorSeq !== seen.current) { seen.current = draft.errorSeq; input.current?.focus(); } }, [draft?.errorSeq]);
  const run = (label: string, call: () => Promise<unknown>) => { if (gated) return; void store.perform({ identity: key, label }, call); };
  const why = lock ? op!.message : again ? "This request came back after you responded. It may be a new request." : changed ? "" : blocked ? "Refresh requests before you respond." : "";
  const guard = { "aria-disabled": gated || undefined, "aria-describedby": `${dom}-state` };
  return <article ref={card} className="request-card" data-card={key} aria-labelledby={`${dom}-title`}>
    <h3 id={`${dom}-title`}>{item.title}</h3>
    <p className="request-detail">{item.detail}</p>
    {failure && <p role="alert" className="error-note">{failure}</p>}
    <p id={`${dom}-state`} className="request-state">{why}</p>
    {lock && (op!.phase === "unknown" || op!.phase === "accepted") && <div className="review-actions">
      <button className="secondary" onClick={() => { void store.check(); }}>Check status</button>
      {op!.checked && <button className="secondary" onClick={() => review(() => store.reopen(key))}>Review and respond again</button>}
    </div>}
    {again && !lock && <div className="review-actions"><button className="secondary" onClick={() => review(() => store.confirmReview(key))}>I have reviewed this request</button></div>}
    {r && draft && <>
      {changed && <div className="request-changed" role="group" aria-label="Request changed">
        <p>This request changed. The ceiling you reviewed was {before ?? "unavailable"}. It is now {current ?? "unavailable"} (version {r.version}).</p>
        <button className="secondary" onClick={() => review(() => store.reviewCeiling(item))}>Review the changed request</button>
      </div>}
      <p>Current ceiling: <strong>{current ?? "unavailable"}</strong></p>
      <label htmlFor={`${dom}-amount`}>New ceiling ({r.currency})</label>
      <input id={`${dom}-amount`} ref={input} inputMode="decimal" autoComplete="off" value={draft.amount} readOnly={gated}
        aria-invalid={draft.error ? true : undefined} aria-describedby={draft.error ? `${dom}-amount-error` : undefined}
        onChange={e => store.setAmount(key, e.target.value)}/>
      {draft.error && <p id={`${dom}-amount-error`} role="alert" className="field-error">{draft.error}</p>}
      <div className="review-actions">
        <button className="secondary" {...guard} onClick={() => { if (!gated) store.answerCeiling(item, "deny"); }}>Keep ceiling</button>
        <button className="primary" {...guard} onClick={() => { if (!gated) store.answerCeiling(item, "approve"); }}>Raise ceiling</button>
      </div>
    </>}
    {item.kind === "unknown_outcome" && <div className="review-actions">{[["ran", "It ran"], ["not_run", "It did not run"], ["dismissed", "Dismiss"]].map(([decision, label]) => <button key={decision} className="secondary" {...guard} onClick={() => run(label, () => api.botDecide(item.id, decision, "", id))}>{label}</button>)}</div>}
    {item.kind === "budget_hold" && <div className="review-actions"><button className="secondary" {...guard} onClick={() => run("Release hold", () => api.botApprove(id))}>Release hold without changing ceiling</button></div>}
    {item.kind === "held_task" && <div className="review-actions"><button className="secondary" {...guard} onClick={() => run("Retry task", () => api.botRetryTask(item.id, id))}>Retry task</button></div>}
    {(item.kind === "waiting_task" || item.kind === "held_task") && <div className="review-actions"><button className="secondary" {...guard} onClick={() => run("Cancel task", () => api.botCancelTask(item.task_id ?? item.id, id))}>Cancel task</button></div>}
  </article>;
}

export function Requests({ id, row, protectedInput, requests, disabled, refresh }: {
  id: string; row?: api.SetupRecord; protectedInput: boolean; requests: ReturnType<typeof useRequests>;
  disabled: boolean; refresh: () => Promise<void>;
}) {
  const { store, view } = requests;
  const reload = async () => { await requests.refresh(); await refresh(); };
  const keys = [...(row?.setup.wait ? [`setup:${row.setup.wait.request_id}`] : []), ...view.secrets.map(request => `secret:${request.request_id}`), ...view.items.map(itemKey)];
  const root = useRef<HTMLDivElement>(null), heading = useRef<HTMLHeadingElement>(null), empty = useRef<HTMLParagraphElement>(null);
  const active = useRef<{ key: string; index: number } | null>(null);
  // Focus follows the owner only while they stay in this tab. A removed card hands focus to the next card.
  useLayoutEffect(() => {
    const was = active.current;
    if (!was || keys.includes(was.key)) return;
    const now = document.activeElement;
    if (now && now !== document.body && root.current?.contains(now) === false) { active.current = null; return; }
    active.current = null;
    const next = keys[Math.min(was.index, keys.length - 1)];
    const card = next ? [...(root.current?.querySelectorAll<HTMLElement>("[data-card]") ?? [])].find(node => node.dataset.card === next) : null;
    const target = card?.querySelector<HTMLElement>("button:not([aria-disabled=true]), input") ?? card?.querySelector<HTMLElement>("h3") ?? empty.current ?? heading.current;
    if (target && !target.hasAttribute("tabindex") && target.tagName === "H3") target.tabIndex = -1;
    target?.focus({ preventScroll: false });
  }, [keys.join("|")]);
  const blocked = disabled || !!view.error;
  const secure = !!row?.setup.wait || view.secrets.length > 0;
  const text = view.notice.text || (!view.loaded && !view.error ? "Loading requests…" : "");
  return <div className="requests-view" ref={root}
    onFocus={e => { const card = (e.target as HTMLElement).closest<HTMLElement>("[data-card]"); active.current = card ? { key: card.dataset.card!, index: keys.indexOf(card.dataset.card!) } : null; }}
    onBlur={e => { const node = e.target as HTMLElement; queueMicrotask(() => { if (node.isConnected) active.current = null; }); }}>
    <h2 ref={heading} tabIndex={-1}>Requests</h2>
    <p role="status" aria-live="polite" className={`requests-notice ${view.notice.tone}`}>{text}</p>
    {view.error && <p className="error-note" role="alert">{view.error}{view.items.length > 0 && " The requests below may be out of date."}<button className="text-button" onClick={() => { void reload(); }}>Refresh requests</button></p>}
    {secure && <fieldset className="requests-secure" disabled={blocked}>
      {row?.setup.wait && <div data-card={`setup:${row.setup.wait.request_id}`}><WaitPanel key={row.setup.wait.request_id} row={row} protectedInput={protectedInput} refresh={reload} response={requests.challenge}/></div>}
      {view.secrets.map(request => <div key={request.request_id} data-card={`secret:${request.request_id}`}><TaskSecretPanel botId={id} request={request} refresh={reload}/></div>)}
    </fieldset>}
    {view.items.map(item => <Request key={itemKey(item)} item={item} id={id} store={store} view={view} blocked={blocked}/>)}
    {view.loaded && !view.error && !row?.setup.wait && !view.secrets.length && !view.items.length && <p className="empty-result" ref={empty} tabIndex={-1}>Nothing needs your attention</p>}
  </div>;
}
