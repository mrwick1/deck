import { describe, expect, it, vi } from "vitest";
import type { SetupRecord } from "../lib/api";
import { deliverSecret, guarded } from "../lib/wait";
import { checkAttempts, pauseActions, phaseLabel, resumeBlock, setupHeading, setupKey, stepLines } from "./setup-progress";

const row = (phase: SetupRecord["phase"], version: number): SetupRecord => ({ version, chat_id: 1, phase, runtime_ready: false, error: null, checklist: [], setup: { id: "bot-one", wait: null, max_attempts: 3, steps: [], draft: {} as never } });
const held = () => { let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; }); return { gate, release }; };

describe("setup screen identity", () => {
  it("keeps one key for one setup across version updates", () => {
    for (const phase of ["Provisioning", "Waiting", "Investigate", "Exhausted", "Ready"] as const)
      expect(setupKey(row(phase, 4)), phase).toBe(setupKey(row(phase, 5)));
  });
  it("keys different setups apart", () => {
    expect(setupKey({ ...row("Waiting", 1), setup: { ...row("Waiting", 1).setup, id: "bot-two" } })).not.toBe(setupKey(row("Waiting", 1)));
  });
});

describe("challenge answer guard", () => {
  it("sends once for two synchronous calls and frees the guard after the first settles", async () => {
    const { gate, release } = held(), guard = { busy: false }, send = vi.fn(() => gate);
    const first = guarded(guard, send), second = guarded(guard, send);
    expect(send).toHaveBeenCalledTimes(1);
    expect(await second).toBe(false);
    release();
    expect(await first).toBe(true);
    expect(guard.busy).toBe(false);
    await guarded(guard, async () => {});
  });
  it("frees the guard when the request fails", async () => {
    const guard = { busy: false };
    await expect(guarded(guard, async () => { throw new Error("no"); })).rejects.toThrow("no");
    expect(guard.busy).toBe(false);
  });
});

describe("duplicate protected delivery", () => {
  it("flags a duplicate while the first send is held, without reading or clearing again", async () => {
    const { gate, release } = held(), guard = { busy: false }, read = vi.fn(() => "synthetic-value"), clear = vi.fn(), send = vi.fn(() => gate);
    const first = deliverSecret({ read, clear, guard, send });
    const second = await deliverSecret({ read, clear, guard, send });
    expect(second).toMatchObject({ ok: false, duplicate: true });
    expect(read).toHaveBeenCalledTimes(1);
    expect(clear).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect(guard.busy).toBe(true);
    release();
    expect(await first).toEqual({ ok: true });
    expect(guard.busy).toBe(false);
  });
});

describe("phase label", () => {
  it("says ready when the runtime is ready", () => {
    expect(phaseLabel({ ...row("Ready", 1), runtime_ready: true })).toBe("Your bot is ready");
  });
  it("says preparing for Ready before the runtime is ready", () => {
    expect(phaseLabel(row("Ready", 1))).toBe("Preparing the runtime");
  });
  it("says review for Draft", () => {
    expect(phaseLabel(row("Draft", 1))).toBe("Review setup");
  });
  it("shows the raw phase for Provisioning", () => {
    expect(phaseLabel(row("Provisioning", 1))).toBe("Provisioning");
  });
});

describe("pause and resume actions", () => {
  it("shows resume only when the bot is paused", () => {
    expect(pauseActions(true)).toEqual({ pause: false, resume: true });
  });
  it("shows pause only when the bot runs", () => {
    expect(pauseActions(false)).toEqual({ pause: true, resume: false });
  });
  it("shows both when the pause state is unknown", () => {
    expect(pauseActions(undefined)).toEqual({ pause: true, resume: true });
  });
});

describe("attempt limit check", () => {
  const bad = (text: string, current = 3) => { const r = checkAttempts(text, current); expect(r.ok, text).toBe(false); return r.ok ? "" : r.message; };
  it("accepts a whole number above the limit up to the server u32 maximum", () => {
    expect(checkAttempts("4", 3)).toEqual({ ok: true, value: 4 });
    expect(checkAttempts("4294967295", 3)).toEqual({ ok: true, value: 4294967295 });
  });
  it("rejects empty, fractional, too low and too large input with a message that names the rule", () => {
    expect(bad("")).toMatch(/whole number/i);
    expect(bad("4.5")).toMatch(/whole number/i);
    expect(bad("1e1")).toMatch(/whole number/i);
    expect(bad("3")).toMatch(/above 3/);
    expect(bad("4294967296")).toMatch(/4294967295/);
  });
});

describe("resume and header rules", () => {
  const withWait = (kind: unknown): SetupRecord => ({ ...row("Waiting", 1), setup: { ...row("Waiting", 1).setup, wait: { request_id: "r", kind: kind as never } } });
  it("blocks Resume for an unknown outcome and an unsupported challenge only", () => {
    expect(resumeBlock(row("Investigate", 1))).toMatch(/unknown outcome/);
    expect(resumeBlock(withWait({ Unsupported: { reason: "x" } }))).toMatch(/cannot complete/);
    expect(resumeBlock(withWait("PhoneApproval"))).toBeNull();
    expect(resumeBlock(row("Exhausted", 1))).toBeNull();
  });
  it("titles Draft as review, later phases as progress and an unknown row as Setup", () => {
    expect(setupHeading(row("Draft", 1))).toBe("Review setup");
    expect(setupHeading(row("Waiting", 1))).toBe("Setup progress");
    expect(setupHeading(undefined)).toBe("Setup");
  });
  it("lists steps that are not done as text with their error", () => {
    const r = row("Investigate", 1); r.setup.steps = [{ step: "Allocate", status: "Done", last_error: null }, { step: { Credential: "Mail" }, status: "Unknown", last_error: "timed out" }];
    expect(stepLines(r)).toEqual(["Credential Mail: Unknown. timed out"]);
  });
});
