import { ApiError, type Chat, type StoredMessage } from "./api";
import { parseMessages, visibleItems, type RunView, type Item, type ViewState } from "./events";

/** What the server answers to a message. */
export interface SendResult {
  run_id?: number;
  task_id?: string;
  kind?: string;
  held?: string | null;
}

export interface SendApi {
  createChat(title: string): Promise<Chat>;
  sendMessage(
    id: number,
    text: string,
    o?: { mode?: "steer" | "queue"; priority?: number },
  ): Promise<SendResult>;
}

export const UNCONFIRMED =
  "Sending was not confirmed. Check the conversation before sending again.";

export const UNCONFIRMED_CREATE =
  "Starting the conversation was not confirmed. It may exist. Check Conversations before starting another.";

/** A send that failed. `ambiguous` means the server may still have stored the message. */
export class SendFailure extends Error {
  constructor(
    message: string,
    public ambiguous: boolean,
    /** The chat the message went to. Set once the chat exists, even when the send failed. */
    public chatId: number | null,
    public text: string,
  ) {
    super(message);
  }
}

/** A refused send: a send is in flight, or an earlier one is unresolved. Nothing was sent. */
export class SendBlocked extends Error {
  constructor(
    message: string,
    public reason: "busy" | "unresolved",
  ) {
    super(message);
  }
}

/** Only a clear refusal (4xx) proves the message was not stored. A network error, a 5xx or a bad body does not. */
export const isAmbiguous = (e: unknown) =>
  !(e instanceof ApiError) || e.status === 0 || e.status === 408 || e.status >= 500;

const describe = (e: unknown, ambiguous: boolean) =>
  ambiguous
    ? UNCONFIRMED
    : e instanceof ApiError && e.status === 409
      ? "A run is already active"
      : (e as Error).message;

/**
 * Send one message. A new chat is created first. `onCreated` runs at once when
 * the chat exists, so the caller keeps its id even when the send then fails.
 * The message is sent once. It is never replayed here.
 */
export async function performSend(
  a: SendApi,
  chatId: number | null,
  text: string,
  opts: { mode?: "steer" | "queue"; priority?: number },
  onCreated: (chat: Chat) => void,
): Promise<{ chatId: number; result: SendResult }> {
  let id = chatId;
  if (id === null) {
    try {
      const chat = await a.createChat(text.slice(0, 60));
      id = chat.id;
      onCreated(chat);
    } catch (e) {
      // No message was stored: it goes after the create. A lost answer may still
      // have left a chat that has no id here, so that case is ambiguous.
      const ambiguous = isAmbiguous(e);
      throw new SendFailure(ambiguous ? UNCONFIRMED_CREATE : describe(e, false), ambiguous, null, text);
    }
    opts = {};
  }
  try {
    return { chatId: id, result: await a.sendMessage(id, text, opts) };
  } catch (e) {
    const ambiguous = isAmbiguous(e);
    throw new SendFailure(describe(e, ambiguous), ambiguous, id, text);
  }
}

/**
 * Apply the answer of a send to the view. A run the stream already shows
 * stays as it is. A run that already ended stays out of the view. An empty
 * running state must not replace its items or its end.
 */
export function confirmRun(view: ViewState, runId: number, finished: ReadonlySet<number> = new Set()): ViewState {
  // The run ended and left the view before this answer came. It is not running.
  if (finished.has(runId)) return view;
  const run = view.run;
  if (run?.runId === runId) return view;
  if (run && run.runId === undefined && run.status === "running")
    return { ...view, run: { ...run, runId } };
  return { ...view, run: { runId, items: [], status: "running" } };
}

/** Whether a read of the history holds no more copies of the text than before the send. Then the message is not in it yet. */
export const needsAppend = (items: Item[], text: string, baseline: number) =>
  items.filter((i) => i.kind === "user" && i.text === text).length <= baseline;

export const countUser = (messages: StoredMessage[], text: string) =>
  parseMessages(messages).filter((i) => i.kind === "user" && i.text === text)
    .length;

/**
 * What a read of the chat says about an unconfirmed message. "found" means more
 * stored messages have this text than before. It does not prove this send stored
 * it: another sender may have sent the same text. "not-found" does not prove the
 * server dropped it: the send may still land.
 */
export const reconcile = (
  messages: StoredMessage[],
  text: string,
  baseline: number,
): "found" | "not-found" =>
  countUser(messages, text) > baseline ? "found" : "not-found";

/** Read the stored messages. A failure is a result, never an empty chat. */
export async function loadHistory(
  list: (id: number) => Promise<StoredMessage[]>,
  chatId: number,
): Promise<
  { ok: true; items: ReturnType<typeof parseMessages> } | { ok: false; error: string }
> {
  try {
    return { ok: true, items: parseMessages(await list(chatId)) };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

/** Merge an unconfirmed user message with the visible run. */
export function sendItems(history: Item[], run: RunView | null, pending: { text: string; baseline: number } | null): Item[] {
  const shown = visibleItems(history, run);
  if (!pending || !needsAppend(history, pending.text, pending.baseline)) return shown;
  return visibleItems([...history, { kind: "user", text: pending.text }], run);
}
