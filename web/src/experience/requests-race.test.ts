import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import * as api from "../lib/api";
import { RequestsProvider, type RequestSessions, useRequests } from "./Requests";
import { itemKey } from "./requests-store";

vi.mock("../lib/api", async importOriginal => ({ ...(await importOriginal<typeof import("../lib/api")>()), getInbox: vi.fn(), listSecretRequests: vi.fn() }));
const gate = <T,>() => { let go!: (v: T) => void; const p = new Promise<T>(r => { go = r; }); return { p, go }; };

/** Calls the real hook once in a server render, so the read path runs with no browser. */
function capture() {
  let hook!: ReturnType<typeof useRequests>;
  renderToString(createElement(() => { hook = useRequests("bot"); return null; }));
  return hook;
}

describe("useRequests forced read", () => {
  it("starts a fresh read after an in-flight read settles", async () => {
    const first = gate<api.InboxItem[]>(), second = gate<api.InboxItem[]>();
    const inbox = vi.mocked(api.getInbox).mockReturnValueOnce(first.p).mockReturnValueOnce(second.p);
    vi.mocked(api.listSecretRequests).mockResolvedValue([]);
    const hook = capture();
    const poll = hook.refresh();            // the old poll, started before the write
    const forced = hook.refresh();          // the read after the write
    await Promise.resolve();
    expect(inbox).toHaveBeenCalledTimes(1); // the second read waits for the first
    first.go([]); await poll;
    await vi.waitFor(() => expect(inbox).toHaveBeenCalledTimes(2)); // old code joined the stale read and never made a second one
    second.go([]); await forced;
  });
});

it("keeps operation locks, challenge state and drafts when the workspace remounts", async () => {
  const sessions: RequestSessions = new Map();
  const item: api.InboxItem = { kind: "held_task", id: "task", task_id: "task", title: "Task", detail: "" };
  vi.mocked(api.getInbox).mockReset().mockResolvedValue([item]);
  vi.mocked(api.listSecretRequests).mockReset().mockResolvedValue([]);
  const mount = () => {
    let hook!: ReturnType<typeof useRequests>;
    const Child = () => { hook = useRequests("bot"); return null; };
    renderToString(createElement(RequestsProvider, { sessions, children: createElement(Child) }));
    return hook;
  };
  const first = mount();
  await first.refresh();
  const send = vi.fn(async () => { throw new api.ApiError(0, "Lost reply"); });
  await first.store.perform({ identity: itemKey(item), label: "Retry" }, send);
  first.challenge.set({ rid: "r", pending: true, text: "Sending…", error: false });
  const returned = mount();
  expect(returned.store).toBe(first.store);
  expect(returned.challenge.getSnapshot()?.pending).toBe(true);
  await returned.store.perform({ identity: itemKey(item), label: "Retry" }, send);
  expect(send).toHaveBeenCalledTimes(1);
});
