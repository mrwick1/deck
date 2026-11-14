import { afterEach, describe, expect, it, vi } from "vitest";
import { botAnswerCeiling, botPause, botStop, getBot, getInbox } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("bot control routing", () => {
  it("keeps controls and ceiling responses scoped to the selected bot", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await botPause(true, "first");
    await botStop("second");
    await botAnswerCeiling({ request_id: "request", version: 1, decision: "deny", idempotency_key: "response" }, "second");
    await getInbox("second");
    await getBot();
    expect(fetch.mock.calls.map(c => c[0])).toEqual([
      "/api/bots/first/automation", "/api/bots/second/stop", "/api/bots/second/ceiling-requests/respond", "/api/bots/second/inbox", "/api/bot",
    ]);
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ request_id: "request", version: 1, decision: "deny", idempotency_key: "response" });
  });
});
