import { useEffect, useRef, useState } from "react";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { ArrowDown, ArrowUp, Square } from "lucide-react";
import { Thread } from "../components/assistant-ui/elements/thread.aui";
import { StepRow, StepGroup } from "../components/StepRow";
import { useDeck } from "../lib/useDeck";
import * as api from "../lib/api";
import { describeStep } from "../lib/steps";

const Welcome = () => <div className="chat-empty"><h2>What would you like done?</h2><p>Send an instruction to start this conversation.</p></div>;
const components = { Welcome, ToolFallback: StepRow, ToolGroup: StepGroup };
export function Chat({ id, bot, name, draft, setDraft, disabled, hidden, onCreated, refresh, scrolls, scope }: {
  id: number | null; bot: boolean; name: string; draft: string; setDraft: (text: string) => void;
  disabled: boolean; hidden?: boolean; onCreated: (id: number) => void; refresh: () => Promise<void>;
  scrolls: Map<string, number>; scope: string;
}) {
  const [mode, setMode] = useState<"steer" | "queue">("steer");
  const deck = useDeck(bot ? { chatId: id, mode, priority: 1, home: false } : undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const guard = useRef(false);
  const root = useRef<HTMLDivElement>(null);
  const latestDraft = useRef(draft); latestDraft.current = draft;
  const initialId = useRef(id);
  useEffect(() => { if (!bot && initialId.current !== null) deck.openChat(initialId.current); }, []);
  useEffect(() => {
    if (initialId.current === null && deck.chatId !== null) onCreated(deck.chatId);
  }, [deck.chatId]);
  useEffect(() => {
    if (hidden || deck.loading) return;
    const viewport = root.current?.querySelector<HTMLElement>('[data-slot="aui_thread-viewport"]');
    if (!viewport) return;
    const saved = scrolls.get(scope);
    if (saved !== undefined) viewport.scrollTop = saved;
    const save = () => scrolls.set(scope, viewport.scrollTop);
    viewport.addEventListener("scroll", save);
    return () => { save(); viewport.removeEventListener("scroll", save); };
  }, [hidden, deck.loading, scope, scrolls]);
  const send = async () => {
    const text = draft.trim();
    if (!text || guard.current || disabled || (id !== null && deck.chatId !== id)) return;
    guard.current = true; setBusy(true); setError("");
    try { await deck.sendText(text); if (latestDraft.current.trim() === text) setDraft(""); await refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : "Sending was not confirmed. Check the conversation before retrying."); }
    finally { guard.current = false; setBusy(false); }
  };
  const answer = async (approved: boolean) => {
    if (!deck.dock.approval || guard.current || disabled) return;
    guard.current = true; setBusy(true);
    try { await api.answerApproval(deck.dock.approval.id, approved); }
    catch (e) { setError(e instanceof Error ? e.message : "Approval was not confirmed."); }
    finally { guard.current = false; setBusy(false); }
  };
  return <AssistantRuntimeProvider runtime={deck.runtime}><div className="live-chat" ref={root} hidden={hidden}>
    {!deck.connected && <p className="inline-status" role="status">Reconnecting to chat. Your draft is kept.</p>}
    <div className="message-pane"><Thread hideComposer autoFocus={false} components={components}/></div>
    <button className="latest-button secondary" onClick={() => { const element = root.current?.querySelector('[data-slot="aui_thread-viewport"]'); if (element) element.scrollTop = element.scrollHeight; }}><ArrowDown size={16}/> Latest</button>
    {deck.dock.approval && <section className="approval-request"><strong>{describeStep(deck.dock.approval.name, deck.dock.approval.args, true)}</strong><p>{deck.dock.approval.reason}</p><div className="review-actions"><button className="secondary" disabled={busy || disabled} onClick={() => { void answer(false); }}>Deny</button><button className="primary" disabled={busy || disabled} onClick={() => { void answer(true); }}>Approve</button></div></section>}
    {error && <p className="error-note" role="alert">{error}</p>}
    <form className="compact-composer" onSubmit={e => { e.preventDefault(); void send(); }}>
      <div className="composer-context"><span>To {name}</span>{bot && <select aria-label="Message intent" value={mode} onChange={e => setMode(e.target.value as "steer" | "queue")}><option value="steer">Guide this task</option><option value="queue">Add a task</option></select>}{deck.dock.running && !bot && <button type="button" aria-label="Stop response" disabled={busy || disabled} onClick={() => { if (deck.run?.runId) void api.cancelRun(deck.run.runId).catch(e => setError(e.message)); }}><Square size={16}/></button>}</div>
      <div className="composer-input"><textarea rows={1} maxLength={20000} aria-label={`Message ${name}`} value={draft} onChange={e => setDraft(e.target.value)} placeholder={bot && mode === "queue" ? "Describe the next task" : "Message"}/><button className="send" aria-label="Send message" disabled={busy || disabled || !draft.trim() || (id !== null && deck.chatId !== id)}><ArrowUp size={18}/></button></div>
    </form>
  </div></AssistantRuntimeProvider>;
}
