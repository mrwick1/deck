import {
  useExternalStoreRuntime,
  type AppendMessage,
} from "@assistant-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import * as api from "./api";
import { toThreadMessages } from "./convert";
import {
  applyEvent,
  initialState,
  parseMessages,
  subscribe,
  visibleItems,
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

const fail = (e: unknown) => toast.error((e as Error).message);

/** The chats, the open chat and its live run, as an assistant-ui runtime. */
export function useDeck(bot?: {
  chatId: number | null;
  mode: "steer" | "queue";
  priority: number;
  home: boolean;
}) {
  const [chats, setChats] = useState<api.Chat[]>([]);
  const [chatId, setChatId] = useState<number | null>(null);
  const [history, setHistory] = useState<Item[]>([]);
  const [view, setView] = useState<ViewState>(initialState);
  const [connected, setConnected] = useState(true);
  const [loading, setLoading] = useState(false);
  // The last page of the chat. It stays after the run ends.
  const [page, setPage] = useState<Page | undefined>();
  const viewRef = useRef(view);
  // A chat created by its first message. Its effect starts from that message.
  const seed = useRef<{ chatId: number; text: string; runId: number } | null>(
    null,
  );

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
    put(initialState());
    setConnected(true);
    setPage(undefined);
    if (chatId === null) {
      setHistory([]);
      return;
    }
    let live = true;
    const reload = () =>
      api
        .listMessages(chatId)
        .then((m) => live && setHistory(parseMessages(m)))
        .catch(fail);

    const s = seed.current?.chatId === chatId ? seed.current : null;
    seed.current = null;
    if (s) {
      setHistory([{ kind: "user", text: s.text }]);
      put({ lastId: 0, run: { runId: s.runId, items: [], status: "running" } });
    } else {
      setHistory([]);
      setLoading(true);
      reload().finally(() => live && setLoading(false));
    }

    let timer: ReturnType<typeof setTimeout> | undefined;
    const onEvent = (ev: DeckEvent) => {
      put(applyEvent(viewRef.current, ev));
      if (ev.kind !== "run_finished") return;
      // Replay can finish many runs at once. One reload covers them.
      clearTimeout(timer);
      timer = setTimeout(async () => {
        await reload();
        const run = viewRef.current.run;
        // A failed or cancelled run stays, so its last message shows the status.
        if (live && run?.status === "done") {
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

  const items = useMemo(
    () => visibleItems(history, view.run),
    [history, view.run],
  );
  const messages = useMemo(
    () => toThreadMessages(items, view.run),
    [items, view.run],
  );
  const running = view.run?.status === "running";

  const onNew = useCallback(
    async (m: AppendMessage) => {
      const text = m.content
        .map((p) => (p.type === "text" ? p.text : ""))
        .join("")
        .trim();
      if (!text) return;
      try {
        if (chatId === null) {
          const chat = await api.createChat(text.slice(0, 60));
          const { run_id } = await api.sendMessage(chat.id, text);
          seed.current = { chatId: chat.id, text, runId: run_id! };
          setChats((c) => [chat, ...c]);
          setChatId(chat.id);
          return;
        }
        const isBot = bot?.chatId === chatId;
        const r = await api.sendMessage(
          chatId,
          text,
          isBot ? { mode: bot.mode, priority: bot.priority } : {},
        );
        // A steering message is stored by the run. The others show at once.
        if (!(isBot && r.kind === "steered")) {
          setHistory((h) => [...h, { kind: "user", text }]);
        } else {
          toast.success("Sent to the working task");
        }
        if (r.held) toast.info(`Queued. It waits: ${r.held}.`);
        // The bot's run starts when its turn comes; the event stream shows it then.
        if (r.run_id !== undefined) {
          const v = {
            ...viewRef.current,
            run: { runId: r.run_id, items: [], status: "running" as const },
          };
          viewRef.current = v;
          setView(v);
        }
      } catch (e) {
        const err = e as api.ApiError;
        toast.error(
          err.status === 409 ? "A run is already active" : err.message,
        );
        throw e;
      }
    },
    [chatId, bot?.chatId, bot?.mode, bot?.priority],
  );

  const onCancel = useCallback(async () => {
    const id = viewRef.current.run?.runId;
    if (id !== undefined) await api.cancelRun(id).catch(fail);
  }, []);

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
        onSwitchToNewThread: () => setChatId(null),
        onSwitchToThread: (id) => setChatId(Number(id)),
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
    page,
    browserActive: running && (view.run?.items.some((it) => it.kind === "step" && !it.done && it.name.startsWith("browser_")) ?? false),
    connected: chatId === null || connected,
    dock: { running, startedAt: view.run?.startedAt, approval, answer },
  };
}
