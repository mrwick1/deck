import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createScreen, type Deps, type ScreenState } from "./computer-controller";

const png = (type = "image/png", status = 200) => new Response(status === 204 ? null : new Blob(["x"]), { status, headers: { "content-type": type } });
const bad = () => png("text/html", 500);
const tick = async (ms = 0) => { await vi.advanceTimersByTimeAsync(ms); };

class FakeStream {
  readyState = 1; onerror: (() => void) | null = null; closed = 0;
  fn?: (e: { data: string }) => void;
  addEventListener(_t: "frame", fn: (e: { data: string }) => void) { this.fn = fn; }
  close() { this.closed++; this.readyState = 2; }
  emit(seq: number) { this.fn!({ data: JSON.stringify({ seq }) }); }
  fail(state: number) { this.readyState = state; this.onerror?.(); }
}

function setup(responses: Array<() => Promise<Response> | Response> = []) {
  const streams: FakeStream[] = [], signals: AbortSignal[] = [];
  const revoked: string[] = [], fetched: string[] = [];
  const preloads: Array<{ url: string; ok: () => void; fail: () => void; cancelled: boolean }> = [];
  let n = 0;
  const deps: Deps = {
    openStream: () => { const s = new FakeStream(); streams.push(s); return s; },
    frameUrl: seq => `/frame?seq=${seq}`,
    fetch: async (u, init) => {
      fetched.push(u); signals.push(init.signal);
      const next = responses.shift();
      if (!next) throw new Error("no response");
      return next();
    },
    createObjectURL: () => `blob:${++n}`,
    revokeObjectURL: u => { revoked.push(u); },
    preload: (u, ok, fail) => { const p = { url: u, ok, fail, cancelled: false }; preloads.push(p); return () => { p.cancelled = true; }; },
  };
  const states: ScreenState[] = [];
  const screen = createScreen(deps, s => states.push(s));
  return { screen, streams, signals, revoked, fetched, preloads, states, last: () => states[states.length - 1] };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("computer screen controller", () => {
  it("retries a failed fetch of an unchanged sequence on a bounded schedule, then offers Retry", async () => {
    const t = setup([bad, bad, bad, bad]);
    t.streams[0].emit(7); await tick();
    expect(t.last().phase).toBe("recovering");
    await tick(500); expect(t.fetched).toEqual(["/frame?seq=7", "/frame?seq=7"]);
    await tick(1000); expect(t.fetched.length).toBe(3);
    await tick(2000); expect(t.fetched.length).toBe(4);
    expect(t.last()).toMatchObject({ phase: "failed", frame: null });
    await tick(60000); expect(t.fetched.length).toBe(4);
  });

  it("does not claim recovery when no retry is scheduled", async () => {
    const t = setup([bad, bad, bad, bad]);
    t.streams[0].emit(1); await tick(4000);
    expect(t.last().phase).toBe("failed");
    expect(t.last().message).not.toMatch(/reconnecting|retrying/i);
  });

  it("manual Retry resets the budget and refetches the latest sequence", async () => {
    const t = setup([bad, bad, bad, bad, bad]);
    t.streams[0].emit(3); await tick(4000);
    expect(t.last().phase).toBe("failed");
    t.screen.retry(); await tick();
    expect(t.fetched.length).toBe(5);
    expect(t.last().phase).toBe("recovering");
  });

  it("a new event resets the budget and supersedes the pending fetch", async () => {
    let release!: (r: Response) => void;
    const t = setup([bad, () => new Promise<Response>(r => { release = r; }), () => png()]);
    t.streams[0].emit(1); await tick(); await tick(500);
    expect(t.fetched.length).toBe(2);
    const stale = t.signals[1];
    t.streams[0].emit(2); await tick();
    expect(stale.aborted).toBe(true);
    release(png()); await tick();
    expect(t.preloads.length).toBe(1);
    expect(t.fetched.at(-1)).toBe("/frame?seq=2");
  });

  it("a 204 clears the visible frame at once and stops retries", async () => {
    const t = setup([() => png(), () => png("x", 204)]);
    t.streams[0].emit(1); await tick(); t.preloads[0].ok();
    expect(t.last().frame).toBe("blob:1");
    t.streams[0].emit(2); await tick();
    expect(t.last()).toMatchObject({ frame: null, phase: "hidden" });
    expect(t.revoked).toEqual(["blob:1"]);
    await tick(10000); expect(t.fetched.length).toBe(2);
  });

  it("shows loading, not the image, until the image loads", async () => {
    const t = setup([() => png()]);
    t.streams[0].emit(1); await tick();
    expect(t.last()).toMatchObject({ frame: null, phase: "loading" });
    t.preloads[0].ok();
    expect(t.last()).toMatchObject({ frame: "blob:1", phase: "live" });
  });

  it("undecodable PNG bytes revoke the URL and follow the bounded retry", async () => {
    const t = setup([() => png(), () => png(), () => png(), () => png(), () => png()]);
    t.streams[0].emit(5); await tick();
    for (let i = 0; i < 4; i++) { t.preloads[i].fail(); await tick(2000); }
    expect(t.fetched.length).toBe(4);
    expect(t.revoked).toEqual(["blob:1", "blob:2", "blob:3", "blob:4"]);
    expect(t.last()).toMatchObject({ phase: "failed", frame: null });
    await tick(60000); expect(t.fetched.length).toBe(4);
  });

  it("a decode callback of a superseded frame is ignored", async () => {
    const t = setup([() => png(), () => png()]);
    t.streams[0].emit(1); await tick();
    const first = t.preloads[0];
    t.streams[0].emit(2); await tick();
    expect(first.cancelled).toBe(true);
    first.fail(); first.ok(); await tick(5000);
    expect(t.fetched.length).toBe(2);
    expect(t.last().frame).toBeNull();
  });

  it("an img error on the shown frame revokes it and enters recovery", async () => {
    const t = setup([() => png(), () => png()]);
    t.streams[0].emit(1); await tick(); t.preloads[0].ok();
    t.screen.imageError("blob:1");
    expect(t.last().frame).toBeNull();
    expect(t.revoked).toEqual(["blob:1"]);
    await tick(500); expect(t.fetched.length).toBe(2);
  });

  it("CONNECTING says reconnecting and clears the frame at once", async () => {
    const t = setup([() => png()]);
    t.streams[0].emit(1); await tick(); t.preloads[0].ok();
    t.streams[0].fail(0);
    expect(t.last()).toMatchObject({ frame: null, phase: "reconnecting" });
    expect(t.last().message).toMatch(/Reconnecting/);
  });

  it("CLOSED says stopped, and Retry opens a new stream", async () => {
    const t = setup([]);
    t.streams[0].fail(2);
    expect(t.last().phase).toBe("stopped");
    expect(t.last().message).not.toMatch(/Reconnecting/);
    t.screen.retry();
    expect(t.streams.length).toBe(2);
    expect(t.last().phase).toBe("waiting");
  });

  it("cleanup during fetch, retry wait and decode leaks nothing", async () => {
    for (const stage of ["fetch", "retry", "decode"] as const) {
      const t = setup([stage === "fetch" ? () => new Promise<Response>(() => {}) : stage === "retry" ? bad : () => png()]);
      t.streams[0].emit(1); await tick();
      t.screen.stop();
      expect(t.streams[0].closed).toBe(1);
      expect(t.signals[0].aborted).toBe(true);
      if (stage === "decode") { expect(t.preloads[0].cancelled).toBe(true); expect(t.revoked).toEqual(["blob:1"]); }
      const count = t.states.length;
      t.preloads[0]?.ok(); t.preloads[0]?.fail();
      await tick(10000);
      expect(t.fetched.length).toBe(1);
      expect(t.states.length).toBe(count);
      expect(vi.getTimerCount()).toBe(0);
    }
  });

  it("revokes each URL exactly once", async () => {
    const t = setup([() => png(), () => png(), () => png("x", 204)]);
    t.streams[0].emit(1); await tick(); t.preloads[0].ok();
    t.streams[0].emit(2); await tick(); t.preloads[1].ok();
    t.streams[0].emit(3); await tick();
    t.screen.stop(); t.screen.stop();
    expect([...t.revoked].sort()).toEqual(["blob:1", "blob:2"]);
  });
});
