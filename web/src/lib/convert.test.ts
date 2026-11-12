import { describe, expect, it } from 'vitest';
import { toThreadMessages } from './convert';

describe('toThreadMessages', () => {
  it('groups the items after a user message into one assistant message', () => {
    const msgs = toThreadMessages(
      [
        { kind: 'user', text: 'shot' },
        { kind: 'step', id: 'a', name: 'browser_open', args: { url: 'x' }, done: true, ok: false, summary: 'no' },
        { kind: 'file', fileId: 3, name: 'a.png', mime: 'image/png' },
        { kind: 'assistant', text: 'done', streaming: false },
      ],
      null,
    );
    expect(msgs).toHaveLength(2);
    expect(msgs[1].content).toEqual([
      expect.objectContaining({ type: 'tool-call', toolName: 'browser_open', result: 'no', isError: true }),
      { type: 'image', image: '/api/files/3', filename: 'a.png' },
      { type: 'text', text: 'done' },
    ]);
  });

  it('leaves a running step without a result', () => {
    const [, a] = toThreadMessages(
      [
        { kind: 'user', text: 'x' },
        { kind: 'step', id: 'a', name: 'n', args: {}, done: false },
      ],
      { items: [], status: 'running' },
    );
    expect((a.content as any)[0].result).toBeUndefined();
    expect(a.status).toBeUndefined();
  });

  it('marks the last message of a failed run', () => {
    const msgs = toThreadMessages(
      [
        { kind: 'user', text: 'x' },
        { kind: 'assistant', text: 'hm', streaming: false },
      ],
      { items: [], status: 'failed', error: 'boom' },
    );
    expect(msgs[1].status).toEqual({ type: 'incomplete', reason: 'error', error: 'boom' });
  });
});
