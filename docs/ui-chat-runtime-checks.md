# Chat runtime send and recovery checks

This document describes how `useDeck` sends a message and recovers from a failure.
The logic lives in `web/src/lib/chatRuntime.ts`. The tests are in `chatRuntime.test.ts` and `convert.test.ts`.

## Send contract

- `sendText(text)` sends one message. It throws `SendFailure` or `SendBlocked`. It never shows a toast.
- The `onNew` path of the runtime still shows a toast, unless the caller passes `{ inline: true }` as the second argument of `useDeck`.
- A new chat is created first. `chatId` is set as soon as the create succeeds. A failed send keeps that id, and the next `sendText` uses it.
- The hook keeps its stream and view when it opens the chat it created. It does not reload seeded history and it does not open a second stream.
- The answer of a send never replaces a run the stream already shows. It replaces the view only when the run id differs.
- The hook counts the stored copies of the text before the send. After the answer it adds the message only when the history holds no more copies than that. A read made during the send proves nothing by itself.
- `pending` holds the text while the send runs. `items` already shows it as the last user message. It shows once, also when a read already holds the message.
- A run that finished before the send answered is not shown again as a running run.

## Ambiguous sends

A network error, a 5xx, a 408 or an unreadable answer is ambiguous. The server may have stored the message.

- The hook never sends the message again by itself.
- `unresolvedSend` holds `{ chatId, text, baseline, evidence }`. Sends to that chat throw `SendBlocked` with reason `unresolved`.
- The hook runs one read-only check at once. `checkSend()` runs it again.
- `evidence` is `checking`, `found`, `not-found` or `unreadable`.
- `found` means more stored messages have this text than before. Another sender could have sent the same text. It is not proof.
- `not-found` means the read did not show the message. The send may still land. It is not proof.
- Only `acknowledgeSend()` lifts the block. The UI must call it after the user reviews the chat.

A 4xx error is definite. The message was not stored. The UI keeps the draft and the user may send again.

## History

- A failed read sets `historyError`. The history stays as it was. The UI must show the error, not an empty chat.
- `retryHistory()` reads again. It is read-only.
- A finished run is cleared from the view only after the history read succeeded.

## Stop

`cancelling` is true from the first stop request until the run ends. A second request for the same run is ignored at once. `stop()` throws when the request fails.

## Empty runs

A failed or cancelled run with no output adds an assistant message with the status. A finished or running empty run adds nothing.

## Limits

- A create that fails with a network error, a 5xx or a bad body is ambiguous. `SendFailure` has `chatId` null and `ambiguous` true. A chat may exist that the client cannot name. The UI must hold the draft and must not claim idempotency. Only an idempotency key on the server would fix it.
- `baseline` counts the messages the client loaded. Messages from other senders after that load can make `found` wrong.
- `unresolvedSend` lives in the hook. A page reload loses it.
- The hook has no React test. The send flow is tested as pure functions.
