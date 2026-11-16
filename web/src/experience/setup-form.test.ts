import { describe, expect, it } from "vitest";
import type { SetupDraft } from "../lib/api";
import { ADVANCED, firstInvalid, formatMinor, readAmount, sameDraft, sameFields, toFields, validate } from "./setup-form";

const base: SetupDraft = { revision: 3, name: "Mail", instructions: "Read mail", model: "configured", authority: "Full", resources: { cpus: 2, memory_mib: 1024, disk_gib: 8 }, budget: { currency: "USD", exponent: 2, period: "month", allowance: 0, ceiling: 0 }, credentials: [], task: null };
const check = (patch: Partial<ReturnType<typeof toFields>> = {}, approve = false, draft = base) => validate({ ...toFields(draft), ...patch }, draft, approve);

describe("exact budget amounts", () => {
  it("round-trips safe integers at every exponent from 0 to 9", () => {
    for (let exponent = 0; exponent <= 9; exponent++) for (const minor of [0, 1, 9, 10, 99, 100, 12345, 1234567890123, Number.MAX_SAFE_INTEGER - 1, Number.MAX_SAFE_INTEGER]) {
      const text = formatMinor(minor, exponent);
      expect(readAmount(text, exponent)).toBe(minor);
      expect(text.includes(".") ? text.split(".")[1].length : 0).toBe(exponent);
    }
  });
  it("matches known decimal strings", () => {
    expect(formatMinor(1234, 2)).toBe("12.34");
    expect(formatMinor(5, 3)).toBe("0.005");
    expect(formatMinor(Number.MAX_SAFE_INTEGER, 3)).toBe("9007199254740.991");
    expect(formatMinor(Number.MAX_SAFE_INTEGER, 6)).toBe("9007199254.740991");
    expect(formatMinor(7, 0)).toBe("7");
  });
  it("refuses amounts it cannot show exactly", () => {
    for (const [minor, exponent] of [[-1, 2], [1.5, 2], [Number.MAX_SAFE_INTEGER + 1, 2], [1, 10], [1, -1]]) expect(() => formatMinor(minor, exponent)).toThrow(RangeError);
  });
  it("parses zero, plain decimals and nothing else", () => {
    expect(readAmount("0", 2)).toBe(0);
    expect(readAmount(" 12.34 ", 2)).toBe(1234);
    for (const value of ["", "0.000", "-1", "1e3", ".5", "1.", "9007199254740992"]) expect(readAmount(value, 2)).toBeNull();
    expect(readAmount("1", 10)).toBeNull();
  });
});

describe("shared field validation", () => {
  it("accepts the unchanged draft for Save and Approve", () => {
    expect(check().draft).toEqual(base);
    expect(check({}, true).draft).toEqual(base);
  });
  it("keeps zero spending and an empty restricted list valid", () => {
    const result = check({ restricted: true, allowed: "", allowance: "0", ceiling: "0" }, true);
    expect(result.errors).toEqual({});
    expect(result.draft?.authority).toEqual({ Restricted: [] });
    expect(result.draft?.budget).toMatchObject({ allowance: 0, ceiling: 0 });
  });
  it("rejects blank, fractional, signed, exponent and out-of-range resource text for both actions", () => {
    for (const approve of [false, true]) for (const value of ["", "  ", "0", "-1", "1.5", "1e3", "+2", "0x10", "4294967296", "9007199254740991"]) {
      for (const key of ["cpus", "memory_mib", "disk_gib"] as const) expect(check({ [key]: value }, approve).errors[key], `${key}=${JSON.stringify(value)}`).toBeTruthy();
    }
    expect(check({ cpus: "4294967295" }).errors.cpus).toBeUndefined();
    expect(check({ cpus: " 3 " }).draft?.resources.cpus).toBe(3);
  });
  it("requires a name and instructions for Save, and a model for Approve", () => {
    expect(Object.keys(check({ name: " ", instructions: "" }).errors).sort()).toEqual(["instructions", "name"]);
    expect(check({ model: "" }).errors.model).toBeUndefined();
    expect(check({ model: " " }, true).errors.model).toBeTruthy();
  });
  it("parses the budget exactly and orders allowance below ceiling", () => {
    const draft = { ...base, budget: { ...base.budget!, exponent: 6 } };
    const result = check({ allowance: "1.000001", ceiling: "9007199254.740991" }, true, draft);
    expect(result.draft?.budget).toMatchObject({ allowance: 1000001, ceiling: Number.MAX_SAFE_INTEGER });
    expect(check({ allowance: "2", ceiling: "1" }).errors.allowance).toMatch(/at or below/);
    expect(check({ allowance: "1.234", ceiling: "5" }).errors.allowance).toMatch(/2 decimal/);
  });
  it("blocks approval, not saving, when the server has no budget", () => {
    const none = { ...base, budget: null };
    expect(check({}, false, none).draft?.budget).toBeNull();
    expect(check({}, true, none).errors.budget).toBeTruthy();
  });
  it("trims credential labels, drops blank lines and rejects duplicates", () => {
    expect(check({ credentials: " mail \n\n chat " }).draft?.credentials).toEqual(["mail", "chat"]);
    expect(check({ credentials: "mail\n mail " }).errors.credentials).toBeTruthy();
  });
  it("finds the first invalid field in screen order and knows which are advanced", () => {
    const { errors } = check({ disk_gib: "x", cpus: "y", ceiling: "z" });
    expect(firstInvalid(errors)).toBe("ceiling");
    expect(ADVANCED).toContain("cpus");
    expect(ADVANCED).not.toContain("ceiling");
  });
  it("compares fields by value", () => {
    expect(sameFields(toFields(base), toFields({ ...base }))).toBe(true);
    expect(sameFields(toFields(base), toFields({ ...base, name: "Other" }))).toBe(false);
  });

  it("sees a changed schedule or step limit, and keeps both on save", () => {
    const set = { ...base, schedule: { time: "08:00", timezone: "+05:30" }, max_steps: 40 };
    expect(sameDraft(set, { ...set })).toBe(true);
    expect(sameDraft(set, base)).toBe(false);
    expect(sameDraft(set, { ...set, max_steps: 10 })).toBe(false);
    expect(sameDraft(set, { ...set, schedule: { time: "09:00", timezone: "+05:30" } })).toBe(false);
    const saved = validate(toFields(set), set, false).draft!;
    expect([saved.schedule, saved.max_steps]).toEqual([set.schedule, 40]);
  });
});
