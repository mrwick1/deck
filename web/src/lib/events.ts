import type { StoredMessage } from './api';

export type Item =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming: boolean }
  | {
      kind: 'step';
      id: string;
      name: string;
      args: unknown;
      done: boolean;
      ok?: boolean;
      summary?: string;
    }
  | { kind: 'file'; fileId: number; name: string; mime: string };

export type Status = 'running' | 'done' | 'failed' | 'cancelled';

export interface RunView {
  runId?: number;
  items: Item[];
  status: Status;
  error?: string;
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

const freshRun = (runId?: number): RunView => ({ runId, items: [], status: 'running' });

/** Apply one event. Returns a new state. Persisted events with a seen id are ignored. */
export function applyEvent(state: ViewState, ev: DeckEvent): ViewState {
  if (ev.id !== undefined && ev.id <= state.lastId) return state;
  const lastId = ev.id ?? state.lastId;
  if (ev.kind === 'run_started') {
    return { lastId, run: freshRun(ev.data.run_id) };
  }
  const run = state.run ?? freshRun(ev.data.run_id);
  const items = [...run.items];
  const last = items[items.length - 1];
  switch (ev.kind) {
    case 'text_delta':
      if (last?.kind === 'assistant' && last.streaming) {
        items[items.length - 1] = { ...last, text: last.text + ev.data.text };
      } else {
        items.push({ kind: 'assistant', text: ev.data.text, streaming: true });
      }
      return { lastId, run: { ...run, items } };
    case 'text':
      if (last?.kind === 'assistant' && last.streaming) {
        items[items.length - 1] = { kind: 'assistant', text: ev.data.text, streaming: false };
      } else {
        items.push({ kind: 'assistant', text: ev.data.text, streaming: false });
      }
      return { lastId, run: { ...run, items } };
    case 'tool_call':
      items.push({
        kind: 'step',
        id: ev.data.id,
        name: ev.data.name,
        args: ev.data.args,
        done: false,
      });
      return { lastId, run: { ...run, items } };
    case 'tool_result': {
      const i = items.findIndex((x) => x.kind === 'step' && x.id === ev.data.id);
      if (i >= 0) {
        items[i] = { ...(items[i] as any), done: true, ok: ev.data.ok, summary: ev.data.summary };
      }
      return { lastId, run: { ...run, items } };
    }
    case 'file':
      items.push({
        kind: 'file',
        fileId: ev.data.file_id,
        name: ev.data.name,
        mime: ev.data.mime,
      });
      return { lastId, run: { ...run, items } };
    case 'run_finished':
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
  const steps = new Map<string, Extract<Item, { kind: 'step' }>>();
  for (const m of messages) {
    const v = parseJson(m.content);
    if (!v) continue;
    if (v.type === 'file') {
      items.push({ kind: 'file', fileId: v.file_id, name: v.name, mime: v.mime });
    } else if (v.role === 'user') {
      items.push({ kind: 'user', text: String(v.content ?? '') });
    } else if (v.role === 'assistant') {
      if (v.content) items.push({ kind: 'assistant', text: String(v.content), streaming: false });
      for (const c of v.tool_calls ?? []) {
        const step = {
          kind: 'step' as const,
          id: c.id,
          name: c.function?.name ?? '',
          args: parseJson(c.function?.arguments ?? '') ?? {},
          done: false,
        };
        steps.set(c.id, step);
        items.push(step);
      }
    } else if (v.role === 'tool') {
      const step = steps.get(v.tool_call_id);
      if (step) {
        const body = parseJson(String(v.content ?? ''));
        step.done = true;
        step.ok = body?.ok !== false;
        step.summary = String(v.content ?? '').slice(0, 300);
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
    if (history[i].kind === 'user') {
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
    'run_started',
    'text_delta',
    'text',
    'tool_call',
    'tool_result',
    'file',
    'run_finished',
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
