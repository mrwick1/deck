import { randomBytes } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, listSecretRequests, respondChallenge, sendMessage, submitSecret, submitTaskSecret, type SetupRecord, type SetupWait, type TaskSecretRequest } from "./api";
import { deliverSecret, describeTaskSecret, describeWait, validSecret, waitFor } from "./wait";

afterEach(() => vi.unstubAllGlobals());
const SECRET = randomBytes(18).toString("base64url") + "é word";
const cred = (over: Partial<SetupWait> = {}): SetupWait => ({ request_id: "r1", kind: { Credential: { label: "Fixture login" } }, ...over });
const row = (id: string, wait: SetupWait | null): SetupRecord => ({
  version: 4, chat_id: 1, phase: "Waiting", runtime_ready: false, error: null, checklist: [],
  setup: { id, wait, max_attempts: 3, steps: [], draft: {} as never },
});

describe("protected endpoint", () => {
  it("posts only version, request id and value, to the setup's own route", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await submitSecret("bot a/b", 4, "r1", SECRET);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/setups/bot%20a%2Fb/secret");
    expect(!String(url).includes(SECRET), "protected value escaped in URL").toBe(true);
    const { value, ...metadata } = JSON.parse(init.body);
    expect(metadata).toEqual({ version: 4, request_id: "r1" });
    expect(value === SECRET, "protected body value differs").toBe(true);
    expect(init.method).toBe("POST");
  });
  it("never uses the ordinary message API", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await submitSecret("s", 1, "r", SECRET);
    expect(fetch.mock.calls.every(c => !String(c[0]).includes("/messages"))).toBe(true);
    expect(sendMessage).toBeTypeOf("function");
  });
  it("treats anything but 204 as not delivered and never shows server text", async () => {
    for (const status of [200, 409, 410, 403, 500, 501]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: `bad ${SECRET}` }), { status })));
      const e = await submitSecret("s", 1, "r", SECRET).catch(x => x as ApiError);
      expect(e, String(status)).toBeInstanceOf(ApiError);
      expect(!(e as ApiError).message.includes(SECRET), "protected value escaped in response").toBe(true);
      expect((e as ApiError).message).not.toContain("bad");
    }
  });
  it("reports a network failure without the value", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error(`boom ${SECRET}`)));
    const e = (await submitSecret("s", 1, "r", SECRET).catch(x => x)) as ApiError;
    expect(e.status).toBe(0);
    expect(!e.message.includes(SECRET), "protected value escaped in response").toBe(true);
  });
  it("answers a challenge with metadata only", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await respondChallenge("s", 2, "r9", "completed");
    expect(fetch.mock.calls[0][0]).toBe("/api/setups/s/challenge/respond");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ version: 2, request_id: "r9", decision: "completed" });
  });
});

describe("deliverSecret", () => {
  it("clears the transient value before the request finishes", async () => {
    const order: string[] = [];
    let release!: () => void;
    const send = vi.fn((_value: string) => new Promise<void>(r => { release = r; order.push("send"); }));
    const p = deliverSecret({ read: () => SECRET, clear: () => order.push("clear"), send });
    expect(order).toEqual(["clear", "send"]);
    release();
    expect(await p).toEqual({ ok: true });
    expect(send.mock.calls[0][0] === SECRET, "protected body value differs").toBe(true);
  });
  it("clears again and hides the value after a failure", async () => {
    const clear = vi.fn();
    const r = await deliverSecret({ read: () => SECRET, clear, send: async () => { throw new ApiError(409, "changed"); } });
    expect(clear).toHaveBeenCalledTimes(2);
    expect(r).toEqual({ ok: false, message: "This request changed or expired. Review the current request and enter the value again." });
  });
  it("never displays an arbitrary delivery exception", async () => {
    for (const failure of [new Error(`transport ${SECRET}`), new ApiError(500, `server ${SECRET}`)]) {
      const r = await deliverSecret({ read: () => SECRET, clear: () => {}, send: async () => { throw failure; } });
      expect(r.ok).toBe(false);
      expect(!JSON.stringify(r).includes(SECRET), "protected value escaped through an error").toBe(true);
    }
  });
  it("rejects values above the guest UTF-8 byte limit", async () => {
    const send = vi.fn();
    const r = await deliverSecret({ read: () => "é".repeat(2049), clear: () => {}, send });
    expect(r.ok).toBe(false);
    expect(send).not.toHaveBeenCalled();
  });
  it("refuses an empty or unsafe value without sending", async () => {
    for (const bad of ["", "a\nb", "a\rb", "a\0b"]) {
      const send = vi.fn();
      const clear = vi.fn();
      const r = await deliverSecret({ read: () => bad, clear, send });
      expect(r.ok).toBe(false);
      expect(JSON.stringify(r)).not.toContain("a\\nb");
      expect(send).not.toHaveBeenCalled();
      expect(clear).toHaveBeenCalled();
    }
    expect(validSecret(" keeps spaces ")).toBe(true);
  });
  it("sends one request while one is in flight", async () => {
    let release!: () => void;
    const send = vi.fn(() => new Promise<void>(r => { release = r; }));
    const guard = { busy: false };
    const a = deliverSecret({ read: () => "x", clear: () => {}, send, guard });
    const b = await deliverSecret({ read: () => "x", clear: () => {}, send, guard });
    expect(b.ok).toBe(false);
    release(); await a;
    expect(send).toHaveBeenCalledTimes(1);
    expect(guard.busy).toBe(false);
  });
});

describe("describeWait", () => {
  const now = 1000;
  it("offers protected input only when the server advertises it", () => {
    expect(describeWait(cred(), true, now).mode).toBe("secret");
    const off = describeWait(cred(), false, now);
    expect(off.mode).toBe("blocked");
    expect(off.detail).toMatch(/not available/i);
  });
  it("keeps phone and CAPTCHA waits and shows the instruction", () => {
    const p = describeWait({ request_id: "r", kind: "PhoneApproval", instructions: "Match 42" }, false, now);
    expect(p.mode).toBe("challenge");
    expect(p.detail).toContain("Match 42");
    expect(describeWait({ request_id: "r", kind: "Captcha" }, false, now).mode).toBe("challenge");
  });
  it("does not call owner completion a verified sign-in", () => {
    const d = describeWait({ request_id: "r", kind: "Captcha", answered: true }, false, now);
    expect(d.mode).toBe("pending");
    expect(d.detail).toMatch(/not yet verified|verif/i);
    expect(d.detail).not.toMatch(/signed in successfully/i);
  });
  it("reads the challenge backend fields and keeps answered requests pending past expiry", () => {
    const pending = describeWait({ request_id: "r", kind: "PhoneApproval", instructions: "Approve the fixture", match_number: "17", answered: true, expires_at: 999 }, false, now);
    expect(pending.mode).toBe("pending");
    const open = describeWait({ request_id: "r", kind: "PhoneApproval", instructions: "Approve the fixture", match_number: "17" }, false, now);
    expect(open.detail).toContain("Approve the fixture");
    expect(open.detail).toContain("17");
  });
  it("blocks an expired or unsupported request", () => {
    expect(describeWait(cred({ expires_at: 999 }), true, now).mode).toBe("expired");
    expect(describeWait(cred({ expires_at: 1001 }), true, now).mode).toBe("secret");
    expect(describeWait({ request_id: "r", kind: { Unsupported: { reason: "passkey" } } }, true, now)).toMatchObject({ mode: "blocked" });
  });
});

describe("waitFor", () => {
  it("picks only the active bot's own pending request", () => {
    const rows = [row("a", cred({ request_id: "ra" })), row("b", cred({ request_id: "rb" })), row("c", null)];
    expect(waitFor(rows, "b")?.setup.wait?.request_id).toBe("rb");
    expect(waitFor(rows, "c")).toBeUndefined();
    expect(waitFor(rows, undefined)).toBeUndefined();
  });
});

describe("active-task protected input", () => {
  const req = (over: Partial<TaskSecretRequest> = {}): TaskSecretRequest => ({ request_id: "secret-1", label: "Site pass", purpose: "to sign in", expires_at: 2000, ...over });
  it("lists a bot's own requests from its own route and reads metadata only", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify([req()]), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await listSecretRequests("bot a/b")).toEqual([req()]);
    expect(fetch.mock.calls[0][0]).toBe("/api/bots/bot%20a%2Fb/secret-requests");
  });
  it("posts only request id and value to the bot's task route, never a setup route or a message", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await submitTaskSecret("bot a/b", "secret-1", SECRET);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/bots/bot%20a%2Fb/secret");
    expect(!String(url).includes(SECRET), "protected value escaped in URL").toBe(true);
    const { value, ...metadata } = JSON.parse(init.body);
    expect(metadata).toEqual({ request_id: "secret-1" });
    expect(value === SECRET, "protected body value differs").toBe(true);
    expect(init.method).toBe("POST");
    expect(String(url)).not.toContain("/setups/");
    expect(String(url)).not.toContain("/messages");
  });
  it("counts only 204 as delivered and never shows server text", async () => {
    for (const status of [200, 409, 410, 403, 500, 503]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: `bad ${SECRET}` }), { status })));
      const e = await submitTaskSecret("b", "r", SECRET).catch(x => x as ApiError);
      expect(e, String(status)).toBeInstanceOf(ApiError);
      expect(!(e as ApiError).message.includes(SECRET), "protected value escaped in response").toBe(true);
      expect((e as ApiError).message).not.toContain("bad");
    }
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError(`offline ${SECRET}`)));
    const e = await submitTaskSecret("b", "r", SECRET).catch(x => x as ApiError);
    expect(!(e as ApiError).message.includes(SECRET), "protected value escaped in network error").toBe(true);
  });
  it("shows a form until the request expires, then points to a replacement", () => {
    const open = describeTaskSecret(req(), 1000);
    expect(open.mode).toBe("secret");
    expect(open.title).toContain("Site pass");
    expect(open.detail).toContain("to sign in");
    const gone = describeTaskSecret(req(), 2000);
    expect(gone.mode).toBe("expired");
    expect(gone.detail).toMatch(/ask|again|new request/i);
  });
  it("keeps the shared delivery rules: zero trim, size limit, field cleared first", async () => {
    const send = vi.fn().mockResolvedValue(undefined);
    const clear = vi.fn();
    const padded = `  ${SECRET}  `;
    expect((await deliverSecret({ read: () => padded, clear, send })).ok).toBe(true);
    expect(send.mock.calls[0][0] === padded, "value was trimmed or changed").toBe(true);
    expect(clear).toHaveBeenCalled();
    expect((await deliverSecret({ read: () => "x".repeat(4097), clear, send })).ok).toBe(false);
    expect(send).toHaveBeenCalledTimes(1);
  });
});
