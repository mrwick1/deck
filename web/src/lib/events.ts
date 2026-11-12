import type { StoredMessage } from "./api";

export type Item =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string; streaming: boolean }
  | {
      kind: "reasoning";
      text: string;
      streaming: boolean;
      startedAt?: number;
      endedAt?: number;
    }
  | {
      kind: "step";
      id: string;
      name: string;
      args: unknown;
      done: boolean;
      ok?: boolean;
      summary?: string;
      /** Set when the step waits for, or got, the user's answer. */
      approval?: Approval;
      /** Epoch milliseconds. */
      startedAt?: number;
      endedAt?: number;
    }
  | { kind: "file"; fileId: number; name: string; mime: string };

export interface Approval {
  id: string;
  /** Why the step needs a yes, in the model's words. */
  reason?: string;
  approved?: boolean;
}

/** The page the agent is on, for the live view. */
export interface Page {
  url: string;
  title?: string;
  /** The latest capture of the page. */
  fileId?: number;
}

export type Status = "running" | "done" | "failed" | "cancelled";

export interface RunView {
  runId?: number;
  items: Item[];
  status: Status;
  error?: string;
  page?: Page;
  /** Epoch milliseconds. */
  startedAt?: number;
}

export interface ViewState {
  /** The id of the last persisted event applied. */
  lastId: number;
  run: RunView | null;
}

export interface DeckEvent {
  /** Set on persisted events only. */
  id?: number;
  kind: string;
  data: Record<string, any>;
}

export const initialState = (): ViewState => ({ lastId: 0, run: null });

const freshRun = (runId?: number, startedAt?: number): RunView => ({
  runId,
  items: [],
  status: "running",
  startedAt,
});

/** Apply one event. Returns a new state. Persisted events with a seen id are ignored. */
export function applyEvent(state: ViewState, ev: DeckEvent): ViewState {
  if (ev.id !== undefined && ev.id <= state.lastId) return state;
  const lastId = ev.id ?? state.lastId;
  // The service stamps each event. A live client without the stamp uses its clock.
  const at: number = ev.data.at ?? Date.now();
  if (ev.kind === "run_started") {
    return { lastId, run: freshRun(ev.data.run_id, at) };
  }
  const run = state.run ?? freshRun(ev.data.run_id);
  const items = [...run.items];
  const last = items[items.length - 1];
  switch (ev.kind) {
    case "text_delta":
    case "reasoning_delta":
    case "text":
    case "reasoning": {
      const kind = ev.kind.startsWith("text") ? "assistant" : "reasoning";
      const streaming = ev.kind.endsWith("_delta");
      // Reasoning ends when the reply or a step starts.
      if (
        kind === "assistant" &&
        last?.kind === "reasoning" &&
        last.endedAt === undefined
      ) {
        items[items.length - 1] = { ...last, streaming: false, endedAt: at };
      }
      const prev = items[items.length - 1];
      const timing =
        kind === "reasoning"
          ? { startedAt: at, endedAt: streaming ? undefined : at }
          : {};
      if (prev?.kind === kind && prev.streaming) {
        const text = streaming ? prev.text + ev.data.text : ev.data.text;
        items[items.length - 1] =
          prev.kind === "reasoning"
            ? { ...prev, text, streaming, endedAt: streaming ? undefined : at }
            : { kind, text, streaming };
      } else {
        items.push(
          kind === "reasoning"
            ? { kind, text: ev.data.text, streaming, ...timing }
            : { kind, text: ev.data.text, streaming },
        );
      }
      return { lastId, run: { ...run, items } };
    }
    case "tool_call":
      if (last?.kind === "reasoning" && last.endedAt === undefined) {
        items[items.length - 1] = { ...last, streaming: false, endedAt: at };
      }
      items.push({
        kind: "step",
        id: ev.data.id,
        name: ev.data.name,
        args: ev.data.args,
        done: false,
        startedAt: at,
      });
      return { lastId, run: { ...run, items } };
    case "tool_result": {
      const i = items.findIndex(
        (x) => x.kind === "step" && x.id === ev.data.id,
      );
      if (i >= 0) {
        items[i] = {
          ...(items[i] as any),
          done: true,
          ok: ev.data.ok,
          summary: ev.data.summary,
          endedAt: at,
        };
      }
      return { lastId, run: { ...run, items } };
    }
    case "approval_request":
    case "approval_result": {
      const i = items.findIndex(
        (x) => x.kind === "step" && x.id === ev.data.id,
      );
      if (i >= 0) {
        const step = items[i] as Extract<Item, { kind: "step" }>;
        const approval =
          ev.kind === "approval_request"
            ? { id: String(ev.data.approval_id), reason: ev.data.reason }
            : { id: "", ...step.approval, approved: Boolean(ev.data.approved) };
        items[i] = { ...step, approval };
      }
      return { lastId, run: { ...run, items } };
    }
    case "page": {
      const page: Page = {
        url: ev.data.url,
        title: ev.data.title,
        fileId:
          ev.data.file_id ??
          (ev.data.url === run.page?.url ? run.page?.fileId : undefined),
      };
      return { lastId, run: { ...run, page } };
    }
    case "file":
      items.push({
        kind: "file",
        fileId: ev.data.file_id,
        name: ev.data.name,
        mime: ev.data.mime,
      });
      return { lastId, run: { ...run, items } };
    case "run_finished":
      return {
        lastId,
        run: { ...run, status: ev.data.status, error: ev.data.error },
      };
    default:
      return { ...state, lastId };
  }
}

function parseJson(s: string): any {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

/** Turn the stored messages into items. Tool messages become the result of their step. */
export function parseMessages(messages: StoredMessage[]): Item[] {
  const items: Item[] = [];
  const steps = new Map<string, Extract<Item, { kind: "step" }>>();
  for (const m of messages) {
    const v = parseJson(m.content);
    if (!v) continue;
    if (v.type === "file") {
      items.push({
        kind: "file",
        fileId: v.file_id,
        name: v.name,
        mime: v.mime,
      });
    } else if (v.role === "user") {
      items.push({ kind: "user", text: String(v.content ?? "") });
    } else if (v.role === "assistant") {
      if (v.content)
        items.push({
          kind: "assistant",
          text: String(v.content),
          streaming: false,
        });
      for (const c of v.tool_calls ?? []) {
        const step = {
          kind: "step" as const,
          id: c.id,
          name: c.function?.name ?? "",
          args: parseJson(c.function?.arguments ?? "") ?? {},
          done: false,
        };
        steps.set(c.id, step);
        items.push(step);
      }
    } else if (v.role === "tool") {
      const step = steps.get(v.tool_call_id);
      if (step) {
        const body = parseJson(String(v.content ?? ""));
        step.done = true;
        step.ok = body?.ok !== false;
        step.summary = String(v.content ?? "").slice(0, 300);
      }
    }
  }
  return items;
}

/** The items to show: stored messages, plus the run being shown. */
export function visibleItems(history: Item[], run: RunView | null): Item[] {
  if (!run) return history;
  // The messages of a run follow the last user message. The run view replaces them.
  let end = history.length;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].kind === "user") {
      end = i + 1;
      break;
    }
  }
  return [...history.slice(0, end), ...run.items];
}

/** Subscribe to a chat's events. Reconnects with backoff and resumes after the last persisted id. */
export function subscribe(
  url: (after: number) => string,
  getLastId: () => number,
  onEvent: (ev: DeckEvent) => void,
  onState: (connected: boolean) => void,
): () => void {
  const kinds = [
    "run_started",
    "text_delta",
    "text",
    "reasoning_delta",
    "reasoning",
    "tool_call",
    "approval_request",
    "approval_result",
    "page",
    "tool_result",
    "file",
    "run_finished",
  ];
  let es: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let delay = 500;
  let closed = false;

  const open = () => {
    es = new EventSource(url(getLastId()));
    es.onopen = () => {
      delay = 500;
      onState(true);
    };
    for (const kind of kinds) {
      es.addEventListener(kind, (e) => {
        const m = e as MessageEvent;
        const id = m.lastEventId ? Number(m.lastEventId) : undefined;
        onEvent({ id, kind, data: parseJson(m.data) ?? {} });
      });
    }
    es.onerror = () => {
      // Close and reopen ourselves, so the new url carries the latest id.
      es?.close();
      onState(false);
      if (closed) return;
      timer = setTimeout(open, delay);
      delay = Math.min(delay * 2, 10_000);
    };
  };
  open();
  return () => {
    closed = true;
    clearTimeout(timer);
    es?.close();
  };
}
