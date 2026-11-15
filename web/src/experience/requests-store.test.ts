import { describe, expect, it, vi } from "vitest";
import * as api from "../lib/api";
import { checkAmount, itemKey, money, RequestsStore } from "./requests-store";

const gate = <T,>() => { let go!: (v: T) => void, fail!: (e: unknown) => void; const p = new Promise<T>((a, b) => { go = a; fail = b; }); return { p, go, fail }; };
const ceiling = (version = 1, minor = 500, precision = 2): api.CeilingRequest => ({ id: "c1", version, currency: "USD", precision, current_ceiling_minor: minor, reason: "r" });
const ceilItem = (version = 1, minor = 500): api.InboxItem => ({ kind: "ceiling_request", id: "c1", title: "t", detail: "d", task_id: null, ceiling: ceiling(version, minor) });
const hold: api.InboxItem = { kind: "budget_hold", id: "hold", title: "Hold", detail: "d", task_id: null };
const held: api.InboxItem = { kind: "held_task", id: "t1", title: "Task", detail: "d", task_id: "t1" };
function make(first: api.InboxItem[] = []) {
  let list = first;
  const inbox = vi.fn(async () => list);
  const answer = vi.fn(async (_a: api.CeilingAnswer) => {});
  let n = 0;
  const store = new RequestsStore({ inbox, secrets: async () => [], answer }, () => `key-${++n}`);
  return { store, inbox, answer, set: (next: api.InboxItem[]) => { list = next; } };
}

describe("read ordering", () => {
  it("drops a read that began before a write ended, so a stale card never reappears", async () => {
    const old = gate<api.InboxItem[]>();
    const { store, inbox, set } = make([hold]);
    inbox.mockReturnValueOnce(old.p);
    const poll = store.read();                       // poll started before the write
    const write = store.perform({ identity: itemKey(hold), label: "Release hold" }, async () => { set([]); });
    old.go([hold]);                                  // the poll answers late with the pre-write list
    expect(await poll).toBe("stale");
    await write;
    expect(store.getSnapshot().items).toEqual([]);   // the fresh read won
    expect(inbox).toHaveBeenCalledTimes(2);
  });
  it("shares one queued forced read between callers", async () => {
    const first = gate<api.InboxItem[]>();
    const { store, inbox } = make();
    inbox.mockReturnValueOnce(first.p);
    const a = store.read(), b = store.read(true), c = store.read(true);
    first.go([]); await Promise.all([a, b, c]);
    expect(inbox).toHaveBeenCalledTimes(2);
  });
});

describe("one response at a time", () => {
  it("sends once for two synchronous clicks and keeps the lock across a store read", async () => {
    const { store } = make([hold]);
    await store.read();
    const send = gate<void>(), call = vi.fn(() => send.p);
    const id = itemKey(hold);
    void store.perform({ identity: id, label: "Release hold" }, call);
    void store.perform({ identity: id, label: "Release hold" }, call);
    expect(call).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().ops.get(id)?.phase).toBe("pending");
    send.go();
  });
  it("locks Retry and Cancel of one task together", async () => {
    const { store } = make([held]);
    await store.read();
    const hang = gate<void>(), other = vi.fn(async () => {});
    void store.perform({ identity: itemKey(held), label: "Retry task" }, () => hang.p);
    void store.perform({ identity: itemKey(held), label: "Cancel task" }, other);
    expect(other).not.toHaveBeenCalled();
    hang.go();
  });
  it("keeps a confirmed response locked after the follow-up read fails, and offers no repeat", async () => {
    const { store, inbox } = make([hold]);
    await store.read();
    inbox.mockRejectedValue(new Error("down"));
    const send = vi.fn(async () => {});
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, send);
    const op = store.getSnapshot().ops.get(itemKey(hold))!;
    expect(op.phase).toBe("accepted");
    expect(op.checked).toBe(false);                  // no "respond again" action is offered
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, send);
    expect(send).toHaveBeenCalledTimes(1);
  });
  it("reports a confirmed close only when the next read shows the card gone", async () => {
    const { store, set } = make([hold]);
    await store.read();
    set([]);
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, async () => {});
    expect(store.getSnapshot().ops.size).toBe(0);
    expect(store.getSnapshot().notice.text).toMatch(/confirmed/);
  });
});

describe("a lost reply", () => {
  it("never says the action failed, blocks a replay and checks the state", async () => {
    const { store } = make([hold]);
    await store.read();
    const send = vi.fn(async () => { throw new api.ApiError(0, "Cannot reach the server"); });
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, send);
    const view = store.getSnapshot();
    expect(view.failures.size).toBe(0);
    expect(view.ops.get(itemKey(hold))).toMatchObject({ phase: "unknown", checked: true });
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, send);
    expect(send).toHaveBeenCalledTimes(1);
  });
  it("stays blocked and unchecked when the check read fails", async () => {
    const { store, inbox } = make([hold]);
    await store.read();
    inbox.mockRejectedValue(new Error("down"));
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, async () => { throw new api.ApiError(503, "x"); });
    expect(store.getSnapshot().ops.get(itemKey(hold))).toMatchObject({ phase: "unknown", checked: false });
    inbox.mockResolvedValue([hold]);
    await store.check();
    expect(store.getSnapshot().ops.get(itemKey(hold))?.checked).toBe(true);
  });
  it("treats absence then reappearance of a no-version hold as new and needing review", async () => {
    const { store, set } = make([hold]);
    await store.read();
    set([]);
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, async () => { throw new api.ApiError(500, "x"); });
    expect(store.getSnapshot().ops.size).toBe(0);
    set([hold]);
    await store.read();
    expect(store.getSnapshot().review.has(itemKey(hold))).toBe(true);
    store.confirmReview(itemKey(hold));
    expect(store.getSnapshot().review.size).toBe(0);
  });
  it("treats a 4xx as a final rejection and reads the real state", async () => {
    const { store, inbox } = make([hold]);
    await store.read();
    await store.perform({ identity: itemKey(hold), label: "Release hold" }, async () => { throw new api.ApiError(409, "x"); });
    expect(store.getSnapshot().ops.size).toBe(0);
    expect(store.getSnapshot().failures.get(itemKey(hold))).toMatch(/changed/);
    expect(inbox).toHaveBeenCalledTimes(2);
  });
});

describe("ceiling request", () => {
  it("keeps the draft and the answer key across a version change and demands review", async () => {
    const { store, set, answer } = make([ceilItem(1, 500)]);
    await store.read();
    const id = itemKey(ceilItem());
    store.setAmount(id, "12.50");
    store.answerCeiling(ceilItem(1, 500), "approve");
    await vi.waitFor(() => expect(answer).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(store.getSnapshot().ops.get(id)?.checked).toBe(true));
    const first = answer.mock.calls[0][0];
    expect(first).toMatchObject({ version: 1, amount_minor: 1250, idempotency_key: "key-1" });
    set([ceilItem(2, 900)]);
    await store.read();
    const view = store.getSnapshot();
    expect(view.drafts.get(id)).toMatchObject({ amount: "12.50", reviewed: 1, reviewedMinor: 500 });
    store.answerCeiling(ceilItem(2, 900), "approve");
    expect(answer).toHaveBeenCalledTimes(1);          // no send before the owner reviews version 2
    expect(store.getSnapshot().drafts.get(id)?.error).toMatch(/Review/);
    store.reviewCeiling(ceilItem(2, 900));
    store.setAmount(id, "20");
    store.answerCeiling(ceilItem(2, 900), "approve");
    await vi.waitFor(() => expect(answer).toHaveBeenCalledTimes(2));
    expect(answer.mock.calls[1][0]).toMatchObject({ version: 2, amount_minor: 2000 });
    expect(answer.mock.calls[1][0].idempotency_key).not.toBe(first.idempotency_key);
  });
  it("reuses one key for the same id, version, decision and normalised amount", async () => {
    const { store, answer } = make([ceilItem()]);
    await store.read();
    const id = itemKey(ceilItem());
    store.setAmount(id, "12.5");
    answer.mockRejectedValueOnce(new api.ApiError(0, "lost"));
    store.answerCeiling(ceilItem(), "approve");
    await vi.waitFor(() => expect(store.getSnapshot().ops.get(id)?.checked).toBe(true));
    store.reopen(id);
    store.setAmount(id, "12.50");
    store.answerCeiling(ceilItem(), "approve");
    await vi.waitFor(() => expect(answer).toHaveBeenCalledTimes(2));
    expect(answer.mock.calls[1][0].idempotency_key).toBe(answer.mock.calls[0][0].idempotency_key);
  });
  it("keeps the amount when a 409 returns, and reports a changed request", async () => {
    const { store, answer } = make([ceilItem()]);
    await store.read();
    const id = itemKey(ceilItem());
    store.setAmount(id, "9");
    answer.mockRejectedValueOnce(new api.ApiError(409, "stale"));
    store.answerCeiling(ceilItem(), "approve");
    await vi.waitFor(() => expect(store.getSnapshot().failures.has(id)).toBe(true));
    expect(store.getSnapshot().drafts.get(id)?.amount).toBe("9");
  });
  it("raises the focus counter for an invalid amount and sends nothing", async () => {
    const { store, answer } = make([ceilItem()]);
    await store.read();
    const id = itemKey(ceilItem());
    store.setAmount(id, "abc");
    store.answerCeiling(ceilItem(), "approve");
    expect(store.getSnapshot().drafts.get(id)).toMatchObject({ errorSeq: 1 });
    expect(answer).not.toHaveBeenCalled();
  });
});

describe("amounts", () => {
  const r = ceiling(1, 500);
  it("tells bad syntax from not above", () => {
    expect(checkAmount("1e3", r)).toMatchObject({ kind: "syntax" });
    expect(checkAmount("-5", r)).toMatchObject({ kind: "syntax" });
    expect(checkAmount("", r)).toMatchObject({ kind: "empty" });
    expect(checkAmount("1.234", r)).toMatchObject({ kind: "scale" });
    expect(checkAmount("5", r)).toMatchObject({ kind: "not-above" });
    expect(checkAmount("5.00", r)).toMatchObject({ kind: "not-above" });
    expect(checkAmount("0", r)).toMatchObject({ kind: "not-above" });
    expect(checkAmount("5.01", r)).toEqual({ ok: true, minor: 501 });
  });
  it("is exact near the safe-integer limit", () => {
    const edge = ceiling(1, Number.MAX_SAFE_INTEGER - 1, 0);
    expect(money(Number.MAX_SAFE_INTEGER, 0, "X")).toBe("9007199254740991 X");
    expect(checkAmount("9007199254740991", edge)).toEqual({ ok: true, minor: 9007199254740991 });
    expect(checkAmount("9007199254740992", edge)).toMatchObject({ kind: "large" });
    expect(money(123456789012345, 2, "USD")).toBe("1234567890123.45 USD");
  });
  it("accepts any amount above a zero ceiling", () => {
    expect(checkAmount("0.01", ceiling(1, 0))).toEqual({ ok: true, minor: 1 });
  });
  it("blocks approval when the server amount cannot be shown exactly", () => {
    expect(checkAmount("10", ceiling(1, 5, 12))).toMatchObject({ kind: "unsupported" });
    expect(checkAmount("10", ceiling(1, 2 ** 60, 2))).toMatchObject({ kind: "unsupported" });
    expect(money(5, 12, "X")).toBeNull();
  });
});

it("shares a task identity between held and waiting cards", () => {
  expect(itemKey(held)).toBe(itemKey({ ...held, kind: "waiting_task", id: "another-card-id" }));
});

it("does not bypass the returned-request review through a direct operation call", async () => {
  const { store, set } = make([hold]);
  await store.read();
  set([]);
  await store.perform({ identity: itemKey(hold), label: "Release hold" }, async () => {});
  set([hold]); await store.read();
  expect(store.getSnapshot().notice.text).toMatch(/returned/);
  const send = vi.fn(async () => {});
  await store.perform({ identity: itemKey(hold), label: "Release hold" }, send);
  expect(send).not.toHaveBeenCalled();
});
