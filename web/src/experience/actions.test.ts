import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../lib/api";
import { saveReview, readAmount, destinationOptions, submitReview, reconcile } from "./actions";
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

const saved = (version: number, patch: Partial<api.SetupDraft> = {}, phase: api.SetupRecord["phase"] = "Draft") => ({ ...row, version, phase, setup: { ...row.setup, draft: { ...draft, revision: version - 1, ...patch } } }) as api.SetupRecord;
const lost = () => new api.ApiError(0, "Cannot reach the server");
describe("submit outcomes", () => {
  it("refuses to approve when the saved row differs from the submitted draft", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockResolvedValue([saved(5, { name: "Changed by another client" })]);
    expect(await submitReview(row, draft, true)).toMatchObject({ kind: "conflict" });
    expect(approve).not.toHaveBeenCalled();
  });
  it("reports phases in order and approves the exact saved version once", async () => {
    const order: string[] = [];
    vi.spyOn(api, "editSetup").mockImplementation(async () => { order.push("save"); });
    vi.spyOn(api, "listSetups").mockImplementation(async () => { order.push("read"); return [saved(5)]; });
    vi.spyOn(api, "approveSetup").mockImplementation(async () => { order.push("approve"); });
    expect(await submitReview(row, draft, true, phase => order.push(phase))).toEqual({ kind: "approved" });
    expect(order).toEqual(["save", "read", "approving", "approve"]);
  });
  it("save only returns the saved row without approving", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockResolvedValue([saved(5)]);
    expect(await submitReview(row, draft, false)).toMatchObject({ kind: "saved", row: { version: 5 } });
    expect(approve).not.toHaveBeenCalled();
  });
  it("still confirms a save when the read afterwards fails, and does not read into the result", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockRejectedValue(lost());
    expect(await submitReview(row, draft, false)).toEqual({ kind: "saved", row: null });
  });
  it("sends no approval when the saved row cannot be read, and says the draft is saved", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockRejectedValue(lost());
    expect(await submitReview(row, draft, true)).toMatchObject({ kind: "failed", stage: "approve", saved: null });
    expect(approve).not.toHaveBeenCalled();
  });
  it("reports a refused approval as partial success with the saved row", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockResolvedValue([saved(5)]);
    vi.spyOn(api, "approveSetup").mockRejectedValue(new api.ApiError(400, "resources exceed the configured limits"));
    const result = await submitReview(row, draft, true);
    expect(result).toMatchObject({ kind: "failed", stage: "approve", saved: { version: 5 } });
    expect(result.kind === "failed" && (result.error as Error).message).toContain("limits");
  });
  it("does not retry or read after a definite refusal", async () => {
    const edit = vi.spyOn(api, "editSetup").mockRejectedValue(new api.ApiError(409, "changed"));
    const list = vi.spyOn(api, "listSetups").mockResolvedValue([]);
    expect(await submitReview(row, draft, true)).toMatchObject({ kind: "failed", stage: "save" });
    expect(edit).toHaveBeenCalledOnce();
    expect(list).not.toHaveBeenCalled();
  });
});

describe("lost replies", () => {
  it("confirms a lost save from the server row without writing again", async () => {
    const edit = vi.spyOn(api, "editSetup").mockRejectedValue(lost());
    vi.spyOn(api, "listSetups").mockResolvedValue([saved(5)]);
    const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
    expect(await submitReview(row, draft, true)).toMatchObject({ kind: "saved", recovered: true, row: { version: 5 } });
    expect(edit).toHaveBeenCalledOnce();
    expect(approve).not.toHaveBeenCalled();
  });
  it("reports a lost save that never applied as a failure that is safe to retry", async () => {
    vi.spyOn(api, "editSetup").mockRejectedValue(lost());
    vi.spyOn(api, "listSetups").mockResolvedValue([row]);
    expect(await submitReview(row, draft, false)).toMatchObject({ kind: "failed", stage: "save" });
  });
  it("reports another client's write after a lost save as a conflict", async () => {
    vi.spyOn(api, "editSetup").mockRejectedValue(new api.ApiError(503, "proxy"));
    vi.spyOn(api, "listSetups").mockResolvedValue([saved(5, { name: "Someone else" })]);
    expect(await submitReview(row, draft, false)).toMatchObject({ kind: "conflict", row: { version: 5 } });
  });
  it("stays unconfirmed, and invents no success, while the read keeps failing", async () => {
    vi.spyOn(api, "editSetup").mockRejectedValue(lost());
    vi.spyOn(api, "listSetups").mockRejectedValue(lost());
    expect(await submitReview(row, draft, true)).toEqual({ kind: "unconfirmed", stage: "save", saved: null });
  });
  it("confirms a lost approval from a read and never sends it again", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    const list = vi.spyOn(api, "listSetups").mockResolvedValueOnce([saved(5)]).mockResolvedValueOnce([saved(6, { revision: 4 }, "Provisioning")]);
    const approve = vi.spyOn(api, "approveSetup").mockRejectedValue(lost());
    expect(await submitReview(row, draft, true)).toEqual({ kind: "approved", recovered: true });
    expect(approve).toHaveBeenCalledOnce();
    expect(list).toHaveBeenCalledTimes(2);
  });
  it("reports a lost approval that did not happen, with the draft still saved", async () => {
    vi.spyOn(api, "editSetup").mockResolvedValue();
    vi.spyOn(api, "listSetups").mockResolvedValue([saved(5)]);
    vi.spyOn(api, "approveSetup").mockRejectedValue(lost());
    expect(await submitReview(row, draft, true)).toMatchObject({ kind: "failed", stage: "approve", saved: { version: 5 } });
  });
  it("a later check after a failed read can still settle the outcome", async () => {
    const list = vi.spyOn(api, "listSetups").mockRejectedValueOnce(lost()).mockResolvedValueOnce([saved(5)]);
    expect(await reconcile(row, draft, "save", null)).toMatchObject({ kind: "unconfirmed" });
    expect(await reconcile(row, draft, "save", null)).toMatchObject({ kind: "saved", recovered: true });
    expect(list).toHaveBeenCalledTimes(2);
  });
  it("treats a setup missing from the read as unconfirmed", async () => {
    vi.spyOn(api, "listSetups").mockResolvedValue([]);
    expect(await reconcile(row, draft, "approve", saved(5))).toMatchObject({ kind: "unconfirmed", stage: "approve" });
  });
});

describe("full semantic draft comparison", () => {
  const changed = (budget: Partial<NonNullable<api.SetupDraft["budget"]>> | null) => ({ ...draft, revision: 4, budget: budget === null ? null : { ...draft.budget!, ...budget } });
  const server = (d: api.SetupDraft) => [{ ...row, version: 5, setup: { ...row.setup, draft: d } }];
  const cases: [string, Partial<NonNullable<api.SetupDraft["budget"]>> | null][] = [["currency", { currency: "EUR" }], ["period", { period: "day" }], ["exponent", { exponent: 3 }], ["presence", null]];
  for (const [name, budget] of cases) {
    it(`does not approve a saved row whose budget ${name} differs`, async () => {
      vi.spyOn(api, "editSetup").mockResolvedValue();
      const approve = vi.spyOn(api, "approveSetup").mockResolvedValue();
      vi.spyOn(api, "listSetups").mockResolvedValue(server(changed(budget)));
      expect((await submitReview(row, draft, true)).kind).toBe("conflict");
      expect(approve).not.toHaveBeenCalled();
    });
    it(`does not call a lost save mine when the budget ${name} differs`, async () => {
      vi.spyOn(api, "listSetups").mockResolvedValue(server(changed(budget)));
      expect((await reconcile(row, draft, "save", null)).kind).toBe("conflict");
    });
  }
  it("accepts the same draft whatever the property order", async () => {
    vi.spyOn(api, "listSetups").mockResolvedValue(server({ task: null, credentials: [], budget: { ceiling: 0, allowance: 0, period: "configured-period", exponent: 2, currency: "USD" }, resources: { disk_gib: 8, memory_mib: 1024, cpus: 2 }, authority: "Full", model: "configured", instructions: "Read mail", name: "Mail", revision: 4 }));
    expect((await reconcile(row, draft, "save", null)).kind).toBe("saved");
  });
});
