import { sendItems } from "./chatRuntime";
import { toThreadMessages } from "./convert";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Chat, type StoredMessage } from "./api";
import {
  confirmRun,
  loadHistory,
  needsAppend,
  performSend,
  reconcile,
  SendFailure,
  UNCONFIRMED,
  UNCONFIRMED_CREATE,
  type SendApi,
} from "./chatRuntime";
import { applyEvent, initialState } from "./events";

const chat: Chat = { id: 7, title: "t", created_at: "" };
const stored = (id: number, role: string, content: string): StoredMessage => ({
  id,
  chat_id: 7,
  role,
  content: JSON.stringify({ role, content }),
  created_at: "",
});
const api = (send: SendApi["sendMessage"]): SendApi & { create: any } => {
  const create = vi.fn(async () => chat);
  return { createChat: create, create, sendMessage: vi.fn(send) };
};

describe("confirmRun", () => {
  it("keeps a run the stream already finished", () => {
    let v = applyEvent(initialState(), { id: 1, kind: "run_started", data: { run_id: 4 } });
    v = applyEvent(v, { id: 2, kind: "text", data: { run_id: 4, text: "hi" } });
    v = applyEvent(v, { id: 3, kind: "run_finished", data: { run_id: 4, status: "done" } });
    const out = confirmRun(v, 4);
    expect(out).toBe(v);
    expect(out.run?.status).toBe("done");
    expect(out.run?.items).toHaveLength(1);
  });

  it("keeps a streaming run and replaces a run of another id", () => {
    const v = applyEvent(initialState(), { id: 1, kind: "run_started", data: { run_id: 4 } });
    expect(confirmRun(v, 4)).toBe(v);
    expect(confirmRun(v, 5).run).toEqual({ runId: 5, items: [], status: "running" });
    expect(confirmRun(initialState(), 5).run?.runId).toBe(5);
  });

  it("adds no running run for a run that finished and left the view before the answer came", () => {
    const v = initialState();
    expect(confirmRun(v, 4, new Set([4]))).toBe(v);
    expect(confirmRun(v, 5, new Set([4])).run?.runId).toBe(5);
  });

  it("gives an id to a run that streamed without one", () => {
    const v = applyEvent(initialState(), { kind: "text_delta", data: { text: "a" } });
    const out = confirmRun(v, 9);
    expect(out.run?.runId).toBe(9);
    expect(out.run?.items).toHaveLength(1);
  });
});

describe("performSend", () => {
  it("keeps the created chat when the send fails, and a retry uses the same id", async () => {
    const a = api(async () => {
      throw new ApiError(400, "bad");
    });
    const created = vi.fn();
    const err = await performSend(a, null, "go", {}, created).catch((e) => e);
    expect(err).toBeInstanceOf(SendFailure);
    expect(err).toMatchObject({ chatId: 7, ambiguous: false, text: "go", message: "bad" });
    expect(created).toHaveBeenCalledTimes(1);
    (a.sendMessage as any).mockResolvedValueOnce({ run_id: 2 });
    const ok = await performSend(a, err.chatId, "go", {}, created);
    expect(ok).toEqual({ chatId: 7, result: { run_id: 2 } });
    expect(a.create).toHaveBeenCalledTimes(1);
    expect(created).toHaveBeenCalledTimes(1);
    expect(a.sendMessage).toHaveBeenLastCalledWith(7, "go", {});
  });

  it("sends a bot chat's options and none to a new chat", async () => {
    const a = api(async () => ({ run_id: 1 }));
    await performSend(a, 3, "x", { mode: "queue", priority: 1 }, vi.fn());
    expect(a.sendMessage).toHaveBeenLastCalledWith(3, "x", { mode: "queue", priority: 1 });
    await performSend(a, null, "x", { mode: "queue", priority: 1 }, vi.fn());
    expect(a.sendMessage).toHaveBeenLastCalledWith(7, "x", {});
  });

  it("marks a refused create definite: no chat id and no message", async () => {
    const a = api(async () => ({}));
    a.create.mockRejectedValue(new ApiError(400, "Bad title"));
    const err = await performSend(a, null, "go", {}, vi.fn()).catch((e) => e);
    expect(err).toMatchObject({ chatId: null, ambiguous: false, message: "Bad title" });
    expect(a.sendMessage).not.toHaveBeenCalled();
  });

  it.each([
    ["network", new ApiError(0, "Cannot reach the server")],
    ["5xx", new ApiError(502, "bad gateway")],
    ["bad body", new SyntaxError("Unexpected end of JSON")],
  ])("marks a %s create failure uncertain: a chat may exist without an id", async (_n, e) => {
    const a = api(async () => ({}));
    a.create.mockRejectedValue(e);
    const err = await performSend(a, null, "go", {}, vi.fn()).catch((x) => x);
    expect(err).toMatchObject({ chatId: null, ambiguous: true, message: UNCONFIRMED_CREATE });
    expect(a.sendMessage).not.toHaveBeenCalled();
  });

  it.each([
    ["network", new ApiError(0, "Cannot reach the server")],
    ["5xx", new ApiError(502, "bad gateway")],
    ["bad body", new SyntaxError("Unexpected end of JSON")],
  ])("marks a %s failure ambiguous and sends once", async (_n, e) => {
    const a = api(async () => {
      throw e;
    });
    const err = await performSend(a, 7, "go", {}, vi.fn()).catch((x) => x);
    expect(err).toMatchObject({ ambiguous: true, chatId: 7, message: UNCONFIRMED });
    expect(a.sendMessage).toHaveBeenCalledTimes(1);
  });

  it("maps 409 and does not mark a 4xx ambiguous", async () => {
    const a = api(async () => {
      throw new ApiError(409, "x");
    });
    const err = await performSend(a, 7, "go", {}, vi.fn()).catch((x) => x);
    expect(err).toMatchObject({ ambiguous: false, message: "A run is already active" });
  });
});

describe("reconcile", () => {
  const before = [stored(1, "user", "go"), stored(2, "assistant", "ok")];
  it("finds a message that was applied but whose answer was lost", () => {
    expect(reconcile([...before, stored(3, "user", "go")], "go", 1)).toBe("found");
  });
  it("does not find a message that was not applied", () => {
    expect(reconcile(before, "go", 1)).toBe("not-found");
  });
  it("does not take an old message with the same text as proof", () => {
    expect(reconcile(before, "go", 0)).toBe("found");
    expect(reconcile(before, "go", 1)).toBe("not-found");
  });
});

describe("loadHistory", () => {
  it("returns the error, not an empty chat, then loads on retry", async () => {
    const list = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(500, "down"))
      .mockResolvedValueOnce([stored(1, "user", "go")]);
    expect(await loadHistory(list, 7)).toEqual({ ok: false, error: "down" });
    expect(await loadHistory(list, 7)).toEqual({
      ok: true,
      items: [{ kind: "user", text: "go" }],
    });
  });
});

describe("needsAppend", () => {
  const items = [{ kind: "user" as const, text: "go" }, { kind: "assistant" as const, text: "ok", streaming: false }];
  it("appends when the history holds no more copies than before the send", () => {
    expect(needsAppend(items, "go", 1)).toBe(true);
  });
  it("does not append when a read shows one more copy than the baseline", () => {
    expect(needsAppend([...items, { kind: "user" as const, text: "go" }], "go", 1)).toBe(false);
  });
  it("is not fooled by a read that holds only other messages", () => {
    expect(needsAppend([...items, { kind: "user" as const, text: "other" }], "go", 1)).toBe(true);
  });
});


describe("pending message order", () => {
  it("keeps the user identity before early output and through confirmation", () => {
    const pending = { text: "Question", baseline: 0 };
    const early = applyEvent(applyEvent(initialState(), { kind: "run_started", data: { run_id: 102 } }), { kind: "text_delta", data: { text: "Early reply" } }).run;
    const before = toThreadMessages(sendItems([], null, pending), null);
    const during = toThreadMessages(sendItems([], early, pending), early);
    const confirmed = toThreadMessages(sendItems([{ kind: "user", text: pending.text }], early, null), early);
    expect(during.map(m => m.role)).toEqual(["user", "assistant"]);
    expect(during[0]).toEqual(before[0]);
    expect(confirmed).toEqual(during);
  });
});
