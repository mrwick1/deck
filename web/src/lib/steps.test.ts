import { describe, expect, it } from 'vitest';
import { describeStep } from './steps';

describe('describeStep', () => {
  it('names the host of an opened page', () => {
    expect(describeStep('browser_open', { url: 'https://example.com/a' }, true)).toBe('Opening example.com');
    expect(describeStep('browser_open', { url: 'https://example.com/a' }, false)).toBe('Opened example.com');
  });

  it('describes act by its op', () => {
    expect(describeStep('browser_act', { op: 'type', text: 'hello' }, false)).toBe('Typed “hello”');
    expect(describeStep('browser_act', { op: 'click', ref: 'e1' }, true)).toBe('Clicking');
  });

  it('falls back to the tool name', () => {
    expect(describeStep('browser_new_tool', {}, false)).toBe('Ran browser_new_tool');
  });
});
