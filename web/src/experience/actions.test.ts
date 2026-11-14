import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../lib/api";
import { saveReview, readAmount, destinationOptions } from "./actions";
const draft: api.SetupDraft = { revision: 3, name: "Mail", instructions: "Read mail", model: "configured", authority: "Full", resources: { cpus: 2, memory_mib: 1024, disk_gib: 8 }, budget: { currency: "USD", exponent: 2, period: "configured-period", allowance: 0, ceiling: 0 }, credentials: [], task: null };
const row = { version: 4, phase: "Draft", setup: { id: "bot-one", draft } } as api.SetupRecord;
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
describe("live setup review", () => {
  it("saves without provisioning and approves only the saved revision", async () => {
    const edit = vi.spyOn(api, "editSetup").mockResolvedValue();
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockResolvedValue([{ ...row, version: 5, setup: { ...row.setup, draft: { ...draft, revision: 4 } } }]);
    await saveReview(row, draft, false);
    expect(approve).not.toHaveBeenCalled();
    await saveReview(row, draft, true);
    expect(edit).toHaveBeenCalledWith("bot-one", 4, draft);
    expect(approve).toHaveBeenCalledExactlyOnceWith("bot-one", 5, 4);
  });
  it("does not approve a revision changed by another client", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockResolvedValue([{ ...row, version: 6, setup: { ...row.setup, draft: { ...draft, revision: 5 } } }]);
    await expect(saveReview(row, draft, true)).rejects.toThrow("changed");
    expect(approve).not.toHaveBeenCalled();
  });
  it("does not approve when saving fails", async () => {
    vi.spyOn(api, "editSetup").mockRejectedValue(new api.ApiError(409, "changed"));
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    await expect(saveReview(row, draft, true)).rejects.toThrow("changed");
    expect(approve).not.toHaveBeenCalled();
  });
  it("validates currency precision without floats or invented defaults", () => {
    expect(readAmount("0", 2)).toBe(0);
    expect(readAmount("12.34", 2)).toBe(1234);
    for (const value of ["", "0.000", "-1", "1e3", "9007199254740992"]) expect(readAmount(value, 2)).toBeNull();
  });
  it("offers only ready bot conversations as task destinations", () => {
    const rows = [{ ...row, runtime_ready: false, chat_id: 1 }, { ...row, runtime_ready: true, chat_id: 2, setup: { ...row.setup, id: "ready" } }];
    expect(destinationOptions(rows)).toEqual([{ id: "ready", chatId: 2, name: "Mail" }]);
  });
});

describe("setup HTTP integration", () => {
  it("writes the draft and approves the exact saved server revision", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json([{ ...row, version: 5, setup: { ...row.setup, draft: { ...draft, revision: 4 } } }]))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await saveReview(row, draft, true);
    expect(fetch.mock.calls.map(call => call[0])).toEqual(["/api/setups/bot-one", "/api/setups", "/api/setups/bot-one/approve"]);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ version: 4, draft });
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toEqual({ version: 5, revision: 4 });
  });
  it("preserves authentication failure and sends no approval", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ error: "Sign in required" }, { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    await expect(saveReview(row, draft, true)).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
