import { describe, expect, it } from 'vitest';
import { applyEvent, initialState, parseMessages, visibleItems, type DeckEvent } from './events';

const run = (events: DeckEvent[]) => events.reduce(applyEvent, initialState());

describe('applyEvent', () => {
  it('shows the final text once after deltas', () => {
    const s = run([
      { id: 1, kind: 'run_started', data: { run_id: 1 } },
      { kind: 'text_delta', data: { text: 'po' } },
      { kind: 'text_delta', data: { text: 'ng' } },
      { id: 2, kind: 'text', data: { text: 'pong' } },
    ]);
    expect(s.run?.items).toEqual([{ kind: 'assistant', text: 'pong', streaming: false }]);
  });

  it('pairs a tool result with its call by id', () => {
    const s = run([
      { id: 1, kind: 'run_started', data: { run_id: 1 } },
      { id: 2, kind: 'tool_call', data: { id: 'a', name: 'browser_open', args: { url: 'x' } } },
      { id: 3, kind: 'tool_call', data: { id: 'b', name: 'browser_close', args: {} } },
      { id: 4, kind: 'tool_result', data: { id: 'a', ok: true, summary: 'opened' } },
    ]);
    const [a, b] = s.run!.items as any[];
    expect(a).toMatchObject({ id: 'a', done: true, ok: true, summary: 'opened' });
    expect(b).toMatchObject({ id: 'b', done: false });
  });

  it('adds a file attachment', () => {
    const s = run([
      { id: 1, kind: 'run_started', data: { run_id: 1 } },
      { id: 2, kind: 'file', data: { file_id: 7, name: 'a.png', mime: 'image/png' } },
    ]);
    expect(s.run?.items).toEqual([{ kind: 'file', fileId: 7, name: 'a.png', mime: 'image/png' }]);
  });

  it('keeps the error of a failed run', () => {
    const s = run([
      { id: 1, kind: 'run_started', data: { run_id: 1 } },
      { id: 2, kind: 'run_finished', data: { status: 'failed', error: 'boom' } },
    ]);
    expect(s.run).toMatchObject({ status: 'failed', error: 'boom' });
  });

  it('ignores events it has already seen after a reconnect', () => {
    const events: DeckEvent[] = [
      { id: 1, kind: 'run_started', data: { run_id: 1 } },
      { id: 2, kind: 'text', data: { text: 'hi' } },
      { id: 3, kind: 'tool_call', data: { id: 'a', name: 'n', args: {} } },
    ];
    const s = run([...events, ...events]);
    expect(s.run?.items).toHaveLength(2);
    expect(s.lastId).toBe(3);
  });

  it('does not move lastId on live deltas', () => {
    const s = run([
      { id: 5, kind: 'run_started', data: { run_id: 2 } },
      { kind: 'text_delta', data: { text: 'a' } },
    ]);
    expect(s.lastId).toBe(5);
  });
});

describe('parseMessages', () => {
  const msg = (id: number, role: string, o: unknown) => ({
    id,
    chat_id: 1,
    role,
    content: JSON.stringify(o),
    created_at: '',
  });

  it('builds items and hides raw tool messages', () => {
    const items = parseMessages([
      msg(1, 'user', { role: 'user', content: 'hi' }),
      msg(2, 'assistant', {
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'c', type: 'function', function: { name: 'x', arguments: '{"a":1}' } }],
      }),
      msg(3, 'tool', { role: 'tool', tool_call_id: 'c', content: '{"ok":false,"error":"no"}' }),
      msg(4, 'assistant', { type: 'file', file_id: 3, name: 'f.pdf', mime: 'application/pdf', caption: '' }),
      msg(5, 'assistant', { role: 'assistant', content: 'done' }),
    ]);
    expect(items.map((i) => i.kind)).toEqual(['user', 'step', 'file', 'assistant']);
    expect(items[1]).toMatchObject({ id: 'c', args: { a: 1 }, done: true, ok: false });
  });
});

describe('visibleItems', () => {
  it('replaces the stored messages of the shown run', () => {
    const history = parseMessages([
      { id: 1, chat_id: 1, role: 'user', content: '{"role":"user","content":"q"}', created_at: '' },
      { id: 2, chat_id: 1, role: 'assistant', content: '{"role":"assistant","content":"a"}', created_at: '' },
    ]);
    const s = run([{ id: 1, kind: 'text', data: { text: 'a' } }]);
    expect(visibleItems(history, s.run).map((i) => i.kind)).toEqual(['user', 'assistant']);
    expect(visibleItems(history, null)).toHaveLength(2);
  });
});
