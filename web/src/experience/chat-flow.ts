/** The draft key of a chat. */
export const draftKey = (chatId: number | null) => (chatId === null ? "chat/new" : `chat/${chatId}`);

/**
 * Text typed while the first message was sent belongs to the chat that send created.
 * It moves from the new-chat draft to the created chat. The new-chat draft is then empty.
 */
export function moveDraft(drafts: Record<string, string>, chatId: number): Record<string, string> {
  const typed = drafts["chat/new"];
  if (typed === undefined) return drafts;
  const { "chat/new": _gone, ...rest } = drafts;
  if (!typed.trim()) return rest;
  const key = draftKey(chatId);
  return { ...rest, [key]: rest[key]?.trim() ? `${rest[key]}\n\n${typed}` : typed };
}

/** A failed message goes back in front of anything typed since. Nothing typed is lost. */
export const mergeFailed = (current: string, failed: string) => (current.trim() ? `${failed}\n\n${current}` : failed);

/** Ctrl or Cmd with Enter sends. Plain Enter is a newline. Enter that confirms an IME composition sends nothing. */
export const sendShortcut = (e: { key: string; ctrlKey: boolean; metaKey: boolean; nativeEvent: { isComposing: boolean }; keyCode?: number }) =>
  e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing && e.keyCode !== 229;

/**
 * What the chat session does for the route. `entered` is true on the step into
 * New conversation from another route. The route is "new" while the chat is
 * created by the first send, so only an entry resets the session.
 */
export function routeAction(a: { routeId: number | null; chatId: number | null; entered: boolean; held: boolean }): { open: number | null } | null {
  if (a.routeId !== null) return a.chatId === a.routeId ? null : { open: a.routeId };
  if (a.entered && a.chatId !== null && !a.held) return { open: null };
  return null;
}

/** Latest is for a reader who is away from the end of the conversation. */
export const isAwayFromBottom = (v: { scrollTop: number; clientHeight: number; scrollHeight: number }) =>
  v.scrollHeight - v.scrollTop - v.clientHeight > 80;

/** Where a bubble starts: the offset of the textarea's bottom right corner from the bubble's. */
export const flipFrom = (first: { right: number; bottom: number }, last: { right: number; bottom: number }) => ({
  x: first.right - last.right,
  y: first.bottom - last.bottom,
});
