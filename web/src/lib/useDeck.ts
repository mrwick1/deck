import {
  useExternalStoreRuntime,
  type AppendMessage,
} from "@assistant-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import * as api from "./api";
import {
  confirmRun,
  loadHistory,
  needsAppend,
  sendItems,
  performSend,
  reconcile,
  SendBlocked,
  SendFailure,
} from "./chatRuntime";
import { toThreadMessages } from "./convert";
import {
  applyEvent,
  initialState,
  parseMessages,
  subscribe,
  type DeckEvent,
  type Item,
  type Page,
  type ViewState,
} from "./events";

const SUGGESTIONS = [
  {
    title: "Take a screenshot",
    label: "of a page",
    prompt: "Take a screenshot of example.com and send it to me.",
  },
  {
    title: "Read a page",
    label: "and summarize it",
    prompt: "Open news.ycombinator.com and list the top five stories.",
  },
  {
    title: "Check a site",
    label: "for a change",
    prompt: "Open example.com and tell me what the page says.",
  },
];

/** A step that waits for the user's yes or no. */
export interface PendingApproval {
  id: string;
  name: string;
  args: unknown;
  reason?: string;
}

/** A message the server may or may not have stored. Sends to its chat stay blocked until acknowledged. */
export interface UnresolvedSend {
  chatId: number;
  text: string;
  /** Stored messages with this text before the send. */
  baseline: number;
  /** What the last read-only check saw. It is a hint, not proof. */
  evidence: "checking" | "found" | "not-found" | "unreadable";
}

/** The chats, the open chat and its live run, as an assistant-ui runtime. */
export function useDeck(
  bot?: {
    chatId: number | null;
    mode: "steer" | "queue";
    priority: number;
    home: boolean;
    onSelectBot?: (id: string | undefined) => void;
  },
  /** With `inline`, send and history errors are not toasts. The caller shows them. */
  opts?: { inline?: boolean },
) {
  const fail = (e: unknown) => {
    if (!opts?.inline) toast.error((e as Error).message);
  };
  const [chats, setChats] = useState<api.Chat[]>([]);
  const [chatId, setChatId] = useState<number | null>(null);
  const [history, setHistory] = useState<Item[]>([]);
  const [view, setView] = useState<ViewState>(initialState);
  const [connected, setConnected] = useState(true);
  const [loading, setLoading] = useState(false);
  // The last page of the chat. It stays after the run ends.
  const [page, setPage] = useState<Page | undefined>();
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [unresolved, setUnresolved] = useState<Map<number, UnresolvedSend>>(() => new Map());
  const [cancelId, setCancelId] = useState<number | undefined>();
  const viewRef = useRef(view);
  const historyRef = useRef(history);
  historyRef.current = history;
  const unresolvedRef = useRef(unresolved);
  // The ref changes with the write, not with the next render: a second send can come before it.
  const putUnresolved = (u: UnresolvedSend | null) => {
    const next = new Map(unresolvedRef.current);
    if (u) next.set(u.chatId, u);
    else if (active.current !== null) next.delete(active.current);
    unresolvedRef.current = next;
    setUnresolved(next);
  };
  // The message being sent. It shows as a pending bubble until the answer or a failure.
  const [pending, setPending] = useState<{ chatId: number | null; text: string; baseline: number } | null>(null);
  // The chat id the send path must answer to. It changes before the render does.
  const active = useRef<number | null>(null);
  // A chat created by its first message. Its effect keeps the stream and the view.
  const fresh = useRef<number | null>(null);
  // The runs this chat's stream has finished. A late send answer must not bring one back.
  const finished = useRef(new Set<number>());
  const sending = useRef(false);
  const cancelling = useRef<number | undefined>(undefined);
  const reloadRef = useRef<() => Promise<boolean>>(async () => true);

  // Start on the overview when configured. Opening a bot then selects its chat.
  const opened = useRef(false);
  useEffect(() => {
    if (bot?.chatId == null || opened.current) return;
    opened.current = true;
    if (!bot.home) setChatId(bot.chatId);
  }, [bot?.chatId, bot?.home]);

  useEffect(() => {
    api
      .listChats()
      .then((c) => setChats(c.sort((a, b) => b.id - a.id)))
      .catch(fail);
  }, []);

  useEffect(() => {
    const put = (v: ViewState) => {
      viewRef.current = v;
      setView(v);
    };
    active.current = chatId;
    // A chat this hook created keeps its view and its seeded history.
    const own = chatId !== null && fresh.current === chatId;
    if (!own) fresh.current = null;
    if (!own) {
      put(initialState());
      finished.current = new Set();
    }
    setConnected(true);
    setPage(undefined);
    setHistoryError(null);
    if (chatId === null) {
      setHistory([]);
      return;
    }
    let live = true;
    const reload = async () => {
      const r = await loadHistory(api.listMessages, chatId);
      if (!live) return false;
      if (r.ok) {
        setHistory(r.items);
        setHistoryError(null);
      } else {
        // The history stays as it was. The error shows; the chat is not empty.
        setHistoryError(r.error);
        fail(new Error(r.error));
      }
      return r.ok;
    };
    reloadRef.current = async () => {
      setLoading(true);
      try {
        return await reload();
      } finally {
        if (live) setLoading(false);
      }
    };
    if (!own) {
      setHistory([]);
      void reloadRef.current();
    }

    let timer: ReturnType<typeof setTimeout> | undefined;
    const onEvent = (ev: DeckEvent) => {
      if (ev.kind === "run_finished") {
        const done = ev.data.run_id ?? viewRef.current.run?.runId;
        if (done !== undefined) finished.current.add(done);
      }
      put(applyEvent(viewRef.current, ev));
      if (ev.kind !== "run_finished") return;
      // Replay can finish many runs at once. One reload covers them.
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const ok = await reload();
        const run = viewRef.current.run;
        // A failed or cancelled run stays, so its last message shows the status.
        // Without the reload, the run stays too: its items are the only copy.
        if (live && ok && run?.status === "done") {
          put({ ...viewRef.current, run: null });
        }
      }, 150);
    };
    const stop = subscribe(
      (after) => api.eventsUrl(chatId, after),
      () => viewRef.current.lastId,
      onEvent,
      setConnected,
    );
    return () => {
      live = false;
      clearTimeout(timer);
      stop();
    };
  }, [chatId]);

  useEffect(() => {
    if (view.run?.page) setPage(view.run.page);
  }, [view.run?.page]);

  const items = useMemo(() => {
    return sendItems(history, view.run, pending?.chatId === chatId ? pending : null);
  }, [history, view.run, pending, chatId]);
  const messages = useMemo(
    () => toThreadMessages(items, view.run),
    [items, view.run],
  );
  const running = view.run?.status === "running";

  const sendText = useCallback(
    async (input: string) => {
      const text = input.trim();
      if (!text) return;
      if (sending.current)
        throw new SendBlocked("A message is already being sent.", "busy");
      const u = unresolvedRef.current.get(chatId ?? -1);
      if (u && u.chatId === chatId)
        throw new SendBlocked(
          "An earlier message may or may not have been sent. Check the conversation first.",
          "unresolved",
        );
      sending.current = true;
      // Counted before the send. A read made after it proves nothing about it.
      const baseline = historyRef.current.filter(
        (i) => i.kind === "user" && i.text === text,
      ).length;
      setPending({ chatId, text, baseline });
      const isBot = chatId !== null && bot?.chatId === chatId;
      try {
        const { chatId: id, result: r } = await performSend(
          api,
          chatId,
          text,
          isBot ? { mode: bot.mode, priority: bot.priority } : {},
          (chat) => {
            // Keep the chat at once. A failed send then still leaves its id.
            fresh.current = chat.id;
            active.current = chat.id;
            setChats((c) => [chat, ...c]);
            setPending((p) => p && { ...p, chatId: chat.id });
            setChatId(chat.id);
          },
        );
        if (active.current !== id) return;
        const steered = isBot && r.kind === "steered";
        // A steering message is stored by the run. A history read since the send holds the rest.
        if (!steered) {
          setHistory((h) =>
            needsAppend(h, text, baseline) ? [...h, { kind: "user", text }] : h,
          );
        }
        if (steered) toast.success("Sent to the working task");
        if (r.held) toast.info(`Queued. It waits: ${r.held}.`);
        // The bot's run starts when its turn comes; the event stream shows it then.
        // A run the stream already shows stays as it is.
        if (r.run_id !== undefined) {
          const v = confirmRun(viewRef.current, r.run_id, finished.current);
          viewRef.current = v;
          setView(v);
        }
      } catch (e) {
        if (e instanceof SendFailure && e.ambiguous && e.chatId !== null) {
          const chat = e.chatId;
          putUnresolved({ chatId: chat, text, baseline, evidence: "checking" });
          void check({ chatId: chat, text, baseline });
        }
        throw e;
      } finally {
        sending.current = false;
        setPending(null);
      }
    },
    [chatId, bot?.chatId, bot?.mode, bot?.priority],
  );

  /** A read-only look at the chat. It never sends. */
  const check = async (u: Pick<UnresolvedSend, "chatId" | "text" | "baseline">) => {
    let evidence: UnresolvedSend["evidence"] = "unreadable";
    try {
      const m = await api.listMessages(u.chatId);
      evidence = reconcile(m, u.text, u.baseline);
      if (active.current === u.chatId) {
        setHistory(parseMessages(m));
        setHistoryError(null);
      }
    } catch {
      /* The evidence stays "unreadable". */
    }
    const cur = unresolvedRef.current.get(u.chatId);
    if (cur && cur.chatId === u.chatId && cur.text === u.text)
      putUnresolved({ ...cur, evidence });
  };
  const checkSend = useCallback(() => {
    const u = unresolvedRef.current.get(active.current ?? -1);
    if (!u) return;
    putUnresolved({ ...u, evidence: "checking" });
    return check(u);
  }, []);
  /** The user reviewed the chat. Sends to it are allowed again. */
  const acknowledgeSend = useCallback(() => putUnresolved(null), []);

  const onNew = useCallback(
    async (m: AppendMessage) => {
      try {
        await sendText(
          m.content.map((p) => (p.type === "text" ? p.text : "")).join(""),
        );
      } catch (e) {
        // The runtime has no inline error. A caller of sendText shows its own.
        if (!opts?.inline) toast.error((e as Error).message);
        throw e;
      }
    },
    [sendText],
  );

  /** Ask the server to stop the run. A second call for the same run does nothing. Throws when the request fails. */
  const stopRun = useCallback(async () => {
    const id = viewRef.current.run?.runId;
    // Check and set in one step, so a second click cannot send a second request.
    if (id === undefined || cancelling.current === id) return;
    cancelling.current = id;
    setCancelId(id);
    try {
      await api.cancelRun(id);
    } catch (e) {
      cancelling.current = undefined;
      setCancelId(undefined);
      throw e;
    }
  }, []);
  const onCancel = useCallback(
    () => stopRun().catch(fail),
    [stopRun],
  );

  const onRespondToToolApproval = useCallback(
    async ({
      approvalId,
      approved,
    }: {
      approvalId: string;
      approved: boolean;
    }) => {
      await api.answerApproval(approvalId, approved).catch(fail);
    },
    [],
  );

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (m) => m,
    isRunning: running,
    isLoading: loading,
    onNew,
    onCancel,
    onRespondToToolApproval,
    suggestions: SUGGESTIONS,
    adapters: {
      threadList: {
        threadId: chatId === null ? undefined : String(chatId),
        threads: chats.map((c) => ({
          status: "regular",
          id: String(c.id),
          title: c.title,
        })),
        onSwitchToNewThread: () => { bot?.onSelectBot?.(undefined); setChatId(null); },
        onSwitchToThread: (id) => {
          const chat = chats.find(c => c.id === Number(id));
          bot?.onSelectBot?.(chat?.bot_id);
          setChatId(Number(id));
        },
      },
    },
  });

  const approval = useMemo<PendingApproval | undefined>(() => {
    for (const it of view.run?.items ?? []) {
      if (
        it.kind === "step" &&
        it.approval &&
        it.approval.approved === undefined
      ) {
        return {
          id: it.approval.id,
          name: it.name,
          args: it.args,
          reason: it.approval.reason,
        };
      }
    }
  }, [view.run?.items]);

  const answer = useCallback(
    (approvalId: string, approved: boolean) =>
      onRespondToToolApproval({ approvalId, approved }),
    [onRespondToToolApproval],
  );

  const title = chats.find((c) => c.id === chatId)?.title ?? "deck";
  return {
    runtime,
    chatId,
    openHome: () => setChatId(null),
    openChat: setChatId,
    title,
    /** Send one message. Throws `SendFailure` or `SendBlocked`. It does not toast. */
    sendText,
    /** True while the history loads. */
    loading,
    /** The history read failed. It is not an empty chat. */
    historyError,
    retryHistory: () => reloadRef.current(),
    items,
    /** The text being sent, while it is. It is already in `items`. */
    pending: pending && pending.chatId === chatId ? pending.text : null,
    run: view.run,
    /** A message that may or may not be stored. Null when none. Only for the open chat. */
    unresolvedSend: unresolved.get(chatId ?? -1) ?? null,
    checkSend,
    acknowledgeSend,
    /** Stop the run. Throws when the request fails, so the caller can show it. */
    stop: stopRun,
    cancelling: cancelId !== undefined && cancelId === view.run?.runId && running,
    page,
    browserActive: running && (view.run?.items.some((it) => it.kind === "step" && !it.done && it.name.startsWith("browser_")) ?? false),
    connected: chatId === null || connected,
    dock: { running, startedAt: view.run?.startedAt, approval, answer },
  };
}
