import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { AnimatePresence, animate, motion, useReducedMotion } from "motion/react";
import { ArrowDown, ArrowUp, Square } from "lucide-react";
import { Thread } from "../components/assistant-ui/elements/thread.aui";
import { StepRow, StepGroup } from "../components/StepRow";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { SendBlocked, SendFailure } from "../lib/chatRuntime";
import { useDeck } from "../lib/useDeck";
import * as api from "../lib/api";
import { describeStep } from "../lib/steps";
import { draftKey, flipFrom, isAwayFromBottom, mergeFailed, routeAction, sendShortcut } from "./chat-flow";
import "./chat.css";

// The welcome text lives outside the thread, so it can fade without moving the messages.
const components = { Welcome: () => null, ToolFallback: StepRow, ToolGroup: StepGroup };
const VIEWPORT = '[data-slot="aui_thread-viewport"]';
const EVIDENCE = {
  checking: "Checking the conversation…",
  found: "A matching message appeared after the earlier ones. Another sender could have sent the same text.",
  "not-found": "No matching new message has appeared. The message could still arrive.",
  unreadable: "The conversation could not be read just now.",
} as const;

type Notice = { text: string; source: "send" | "approval" | "stop" };
type PutDraft = (key: string, update: (current: string) => string) => void;

/**
 * One chat session: the runtime, the send guard and every recovery state.
 * The state outlives the chat screen, so a held or unconfirmed send is not lost when the user navigates away.
 */
export function useChatSession(o: {
  bot?: { chatId: number | null };
  disabled: boolean;
  refresh: () => Promise<void>;
  putDraft: PutDraft;
  onCreated: (chatId: number, title: string) => void;
}) {
  const [mode, setMode] = useState<"steer" | "queue">("steer");
  const deck = useDeck(o.bot ? { chatId: o.bot.chatId, mode, priority: 1, home: false } : undefined, { inline: true });
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  // A create whose answer was lost: a conversation may exist that has no id here.
  const [createHold, setCreateHold] = useState<{ text: string } | null>(null);
  const guard = useRef(false);
  const creating = useRef(false);
  const latest = useRef(o);
  latest.current = o;

  useEffect(() => {
    if (!creating.current || deck.chatId === null) return;
    creating.current = false;
    latest.current.onCreated(deck.chatId, deck.title);
  }, [deck.chatId]);

  const approvalId = deck.dock.approval?.id;
  // A new request replaces an older error. An answer error does not outlive its request.
  useEffect(() => { setNotice(n => (n?.source === "send" ? n : null)); }, [approvalId]);

  const restore = (key: string, text: string) => latest.current.putDraft(key, current => mergeFailed(current, text));
  const send = async (text: string) => {
    if (guard.current || (deck.chatId === null && createHold)) return;
    guard.current = true; setBusy(true); setNotice(null);
    const from = deck.chatId;
    if (from === null) creating.current = true;
    try {
      await deck.sendText(text);
      latest.current.refresh().catch(() => {});
    } catch (e) {
      // A created chat is announced by the effect above, after the render that holds its id.
      if (!(e instanceof SendFailure) || e.chatId === null) creating.current = false;
      if (e instanceof SendFailure && e.ambiguous) {
        // The text stays in the recovery panel. Putting it back in the draft could send it twice.
        if (e.chatId === null) setCreateHold({ text });
      } else {
        restore(draftKey(e instanceof SendFailure ? e.chatId : from), text);
        if (!(e instanceof SendBlocked && e.reason === "busy")) setNotice({ text: e instanceof Error ? e.message : "The message was not sent.", source: "send" });
      }
    } finally { guard.current = false; setBusy(false); }
  };
  const answer = async (approved: boolean) => {
    const approval = deck.dock.approval;
    if (!approval || guard.current || latest.current.disabled) return;
    guard.current = true; setBusy(true); setNotice(null);
    try { await api.answerApproval(approval.id, approved); }
    catch (e) { setNotice({ text: e instanceof Error ? e.message : "Your answer was not confirmed.", source: "approval" }); }
    finally { guard.current = false; setBusy(false); }
  };
  const stop = () => { setNotice(null); deck.stop().catch(e => setNotice({ text: `Stop was not confirmed. ${e instanceof Error ? e.message : ""}`.trim(), source: "stop" })); };
  const settle = (restoreText: boolean) => {
    const u = deck.unresolvedSend;
    if (u && restoreText) restore(draftKey(u.chatId), u.text);
    if (u) deck.acknowledgeSend();
  };
  const restoreCreate = () => {
    if (!createHold) return;
    restore(draftKey(null), createHold.text);
    setCreateHold(null);
  };
  // A first send that is still held keeps the session, and its New conversation screen, when the user comes back.
  const held = busy || deck.pending !== null;
  return { deck, mode, setMode, busy, held, notice, createHold, send, answer, stop, settle, restoreCreate, refresh: o.refresh,
    blocked: busy || !!deck.unresolvedSend };
}
export type ChatSession = ReturnType<typeof useChatSession>;

/** A bot session stays above the routes; only its view follows the workspace slot. */
export function BotChatHost(p: { id: number | null; name: string; draft: string; setDraft: (text: string) => void; disabled: boolean; hidden: boolean;
  slot: HTMLElement | null; refresh: () => Promise<void>; scrolls: Map<string, number>; scope: string; putDraft: PutDraft }) {
  const session = useChatSession({ bot: { chatId: p.id }, disabled: p.disabled, refresh: p.refresh, putDraft: p.putDraft, onCreated: () => {} });
  return p.slot ? createPortal(<ChatView {...p} bot session={session} ready={p.id !== null && session.deck.chatId === p.id}/>, p.slot) : null;
}

/**
 * The chat of Deck. It sits above the routes and draws into `slot`, the place the route gives it.
 * The session survives route changes, a created id replacing "new" and a visit to another screen.
 */
export function ChatHost(p: { routeId: number | null; route: string; slot: HTMLElement | null; name: string; draft: string; setDraft: (text: string) => void; disabled: boolean;
  onCreated: (id: number, title: string) => void; refresh: () => Promise<void>; scrolls: Map<string, number>; putDraft: PutDraft }) {
  const session = useChatSession({ disabled: p.disabled, refresh: p.refresh, putDraft: p.putDraft, onCreated: p.onCreated });
  const { deck } = session;
  const before = useRef("");
  useEffect(() => {
    const action = routeAction({ routeId: p.routeId, chatId: deck.chatId, entered: before.current !== "chat/new" && p.route === "chat/new", held: session.held });
    before.current = p.route;
    if (action) deck.openChat(action.open);
  }, [p.route, p.routeId, deck.chatId]);
  if (!p.slot) return null;
  return createPortal(<ChatView {...p} id={p.routeId} bot={false} session={session} ready={p.routeId === null || deck.chatId === p.routeId} scope={p.route}/>, p.slot);
}

function Composer({ session, name, bot, draft, setDraft, disabled, blocked, ready, onSubmit, textarea }: { session: ChatSession; name: string; bot: boolean; draft: string; setDraft: (t: string) => void;
  disabled: boolean; blocked: boolean; ready: boolean; onSubmit: () => void; textarea: React.RefObject<HTMLTextAreaElement | null> }) {
  const { deck, mode } = session;
  // Browsers without field-sizing get the same growth, capped at the same height.
  useLayoutEffect(() => {
    const el = textarea.current;
    if (!el || (typeof CSS !== "undefined" && CSS.supports?.("field-sizing", "content"))) return;
    el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);
  return <form className="compact-composer" onSubmit={e => { e.preventDefault(); onSubmit(); }}>
    <div className="composer-context"><span>To {name}</span>{bot && <select aria-label="Message intent" value={mode} onChange={e => session.setMode(e.target.value as "steer" | "queue")}><option value="steer">Guide this task</option><option value="queue">Add a task</option></select>}
      {deck.dock.running && !bot && <button type="button" className="stop-button" aria-label={deck.cancelling ? "Stopping response" : "Stop response"} aria-disabled={deck.cancelling || disabled} onClick={() => { if (!deck.cancelling && !disabled) session.stop(); }}><Square size={16}/>{deck.cancelling && <span>Stopping…</span>}</button>}</div>
    <div className="composer-input"><Textarea ref={textarea} rows={1} maxLength={20000} aria-label={`Message ${name}`} value={draft} onChange={e => setDraft(e.target.value)}
      onKeyDown={e => { if (sendShortcut(e)) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} placeholder={bot && mode === "queue" ? "Describe the next task" : "Message"}/>
      <Button className="send" type="submit" aria-label="Send message" aria-busy={session.busy} disabled={disabled || !ready || blocked || !draft.trim()}><ArrowUp size={18}/></Button></div>
  </form>;
}

function Recovery({ session, createHold }: { session: ChatSession; createHold: ChatSession["createHold"] }) {
  const { deck, notice } = session, u = deck.unresolvedSend;
  const row = (children: ReactNode) => <div className="review-actions">{children}</div>;
  if (u) return <section className="recovery-panel" aria-label="Message not confirmed"><strong>Your message may or may not have been sent.</strong><q>{u.text}</q>
    <p role="status">{EVIDENCE[u.evidence]} This is evidence, not proof. Deck does not send it again by itself.</p>
    {row(<><Button variant="outline" disabled={u.evidence === "checking"} onClick={() => { void deck.checkSend(); }}>Check again</Button><Button variant="outline" onClick={() => session.settle(false)}>It was sent</Button><Button variant="outline" onClick={() => session.settle(true)}>It was not sent. Edit it</Button></>)}</section>;
  if (createHold) return <section className="recovery-panel" aria-label="Conversation not confirmed"><strong>Starting this conversation was not confirmed.</strong><q>{createHold.text}</q>
    <p>A conversation may exist without showing here. Your message was not sent. Check Conversations for an empty one, then take your message back.</p>
    {row(<><Button variant="outline" onClick={() => { session.refresh().catch(() => {}); }}>Refresh conversations</Button><Button variant="outline" onClick={session.restoreCreate}>I checked. Restore my message</Button></>)}</section>;
  if (deck.historyError) return <section className="recovery-panel" role="alert"><strong>This conversation could not be loaded.</strong><p>{deck.historyError}</p>{row(<Button variant="outline" onClick={() => { void deck.retryHistory(); }}>Retry</Button>)}</section>;
  if (notice) return <p className="error-note" role="alert">{notice.text}</p>;
  return null;
}

function ChatView({ session, id, bot, name, draft, setDraft, disabled, hidden, scrolls, scope, ready }: { session: ChatSession; id: number | null; bot: boolean; name: string; draft: string;
  setDraft: (t: string) => void; disabled: boolean; hidden?: boolean; scrolls: Map<string, number>; scope: string; ready: boolean }) {
  const { deck } = session;
  const createHold = !bot && id === null ? session.createHold : null;
  const blocked = session.blocked || !!createHold;
  const reduced = useReducedMotion();
  const root = useRef<HTMLDivElement>(null), textarea = useRef<HTMLTextAreaElement>(null), observer = useRef<MutationObserver | undefined>(undefined);
  const [away, setAway] = useState(false);
  const [settled, setSettled] = useState<number | null>(null);
  const viewport = () => root.current?.querySelector<HTMLElement>(VIEWPORT) ?? null;
  const sawLoading = useRef(false);
  useEffect(() => { if (deck.loading) sawLoading.current = true; else if (sawLoading.current && deck.chatId !== null) { sawLoading.current = false; setSettled(deck.chatId); } }, [deck.loading, deck.chatId]);
  // A chat opened from the list shows no welcome until its history has been read.
  const empty = !deck.items.length && !deck.historyError && !deck.loading && (id === null ? deck.chatId === null || !session.held : settled === id);
  useEffect(() => () => observer.current?.disconnect(), []);

  // Restore the reader's place before paint, without an animated scroll. A hidden tab is not read: its scroll position is 0.
  useLayoutEffect(() => {
    const el = viewport();
    if (hidden || deck.loading || !el) return;
    const saved = scrolls.get(scope);
    // The thread scrolls to the end when it mounts. Put the reader back after that, and keep the saved place until then.
    let settling = saved !== undefined, frame = 0;
    const put = () => { if (saved !== undefined) el.scrollTo({ top: saved, behavior: "instant" }); };
    put();
    const again = () => { put(); if (++frame < 3) frames.push(requestAnimationFrame(again)); else settling = false; };
    const frames = [requestAnimationFrame(again)];
    const update = () => setAway(isAwayFromBottom(el));
    const save = () => { if (!settling && !root.current?.hidden) scrolls.set(scope, el.scrollTop); update(); };
    el.addEventListener("scroll", save);
    const grow = new ResizeObserver(update);
    if (el.firstElementChild) grow.observe(el.firstElementChild);
    update();
    return () => { frames.forEach(cancelAnimationFrame); el.removeEventListener("scroll", save); grow.disconnect(); };
  }, [hidden, deck.loading, scope, scrolls]);

  // The new bubble starts at the textarea and settles into its place. Only transform and opacity change.
  const handoff = (first: DOMRect) => {
    const host = root.current;
    if (!host || reduced) return;
    observer.current?.disconnect();
    const seen = new Set(host.querySelectorAll('[data-role="user"]'));
    const o = new MutationObserver(() => {
      const added = [...host.querySelectorAll<HTMLElement>('[data-role="user"]')].filter(n => !seen.has(n)).at(-1);
      if (!added) return;
      o.disconnect();
      const bubble = added.querySelector<HTMLElement>(".aui-user-message-content") ?? added;
      const { x, y } = flipFrom(first, bubble.getBoundingClientRect());
      bubble.style.transform = `translate(${x}px, ${y}px)`; bubble.style.opacity = ".4";
      void animate(bubble, { x: [x, 0], y: [y, 0], opacity: [0.4, 1] }, { duration: 0.28, ease: [0.22, 1, 0.36, 1] }).finished.then(() => { bubble.style.transform = ""; bubble.style.opacity = ""; });
    });
    observer.current = o; o.observe(host, { childList: true, subtree: true });
    setTimeout(() => o.disconnect(), 1500);
  };
  const submit = () => {
    const text = draft.trim();
    if (!text || disabled || !ready || blocked) return;
    const first = textarea.current?.getBoundingClientRect();
    if (first) handoff(first);
    setDraft("");
    void session.send(text);
  };

  return <AssistantRuntimeProvider runtime={deck.runtime}><div className="live-chat" ref={root} hidden={hidden}>
    {!deck.connected && <p className="inline-status" role="status">Reconnecting to chat. Your draft is kept.</p>}
    <div className="message-pane">
      <Thread hideComposer autoFocus={false} components={components}/>
      <AnimatePresence>{empty && <motion.div key="welcome" className="chat-empty" initial={false} exit={{ opacity: 0 }}><h2>What would you like done?</h2><p>Send an instruction to start this conversation.</p></motion.div>}</AnimatePresence>
      {away && <button type="button" className="latest-button secondary" onClick={() => viewport()?.scrollTo({ top: viewport()!.scrollHeight, behavior: reduced ? "auto" : "smooth" })}><ArrowDown size={16}/> Latest</button>}
    </div>
    {deck.dock.approval && <section className="approval-request"><strong>{describeStep(deck.dock.approval.name, deck.dock.approval.args, true)}</strong><p>{deck.dock.approval.reason}</p><div className="review-actions"><button className="secondary" disabled={session.busy || disabled} onClick={() => { void session.answer(false); }}>Deny</button><button className="primary" disabled={session.busy || disabled} onClick={() => { void session.answer(true); }}>Approve</button></div></section>}
    <Recovery session={session} createHold={createHold}/>
    <Composer session={session} name={name} bot={bot} draft={draft} setDraft={setDraft} disabled={disabled} blocked={blocked} ready={ready} onSubmit={submit} textarea={textarea}/>
  </div></AssistantRuntimeProvider>;
}
