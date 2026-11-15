import type { SetupDraft } from "../lib/api";
import { parseMinor } from "../lib/bot";

/** Client bounds that match the existing form. The server contract has no other text limits. */
export const TEXT_LIMITS = { name: 200, long: 20000 } as const;
const U32_MAX = 4294967295;

export type ResourceKey = "cpus" | "memory_mib" | "disk_gib";
/** Every editable value as the owner typed it. Nothing is parsed until validation. */
export interface Fields {
  name: string; instructions: string; model: string; restricted: boolean; allowed: string;
  allowance: string; ceiling: string; cpus: string; memory_mib: string; disk_gib: string;
  credentials: string; task: string;
}
export type FieldKey = keyof Fields | "budget";
export type Errors = Partial<Record<FieldKey, string>>;
/** Top to bottom on screen. The first invalid field takes focus. */
export const FIELD_ORDER: FieldKey[] = ["name", "instructions", "allowed", "allowance", "ceiling", "model", "cpus", "memory_mib", "disk_gib", "credentials", "task"];
export const ADVANCED: FieldKey[] = ["model", "cpus", "memory_mib", "disk_gib", "credentials", "task"];

/** Read a decimal amount as whole minor units. Zero is valid. Returns null for anything else. */
export function readAmount(text: string, precision: number): number | null {
  if (!Number.isInteger(precision) || precision < 0 || precision > 9) return null;
  const value = text.trim();
  if (!/^\d+(?:\.\d+)?$/.test(value) || (value.split(".")[1]?.length ?? 0) > precision) return null;
  return /^0+(?:\.0+)?$/.test(value) ? 0 : parseMinor(value, precision);
}

/** Write whole minor units as a decimal with integer arithmetic only. Floats lose digits near 2^53. */
export function formatMinor(minor: number, exponent: number): string {
  if (!Number.isSafeInteger(minor) || minor < 0 || !Number.isInteger(exponent) || exponent < 0 || exponent > 9) throw new RangeError("Amount is outside the exact range");
  const digits = BigInt(minor).toString().padStart(exponent + 1, "0");
  return exponent ? `${digits.slice(0, -exponent)}.${digits.slice(-exponent)}` : digits;
}
const amountText = (minor: number, exponent: number) => { try { return formatMinor(minor, exponent); } catch { return ""; } };

export function toFields(draft: SetupDraft): Fields {
  const budget = draft.budget, authority = draft.authority;
  return {
    name: draft.name, instructions: draft.instructions, model: draft.model,
    restricted: authority !== "Full", allowed: authority === "Full" ? "" : authority.Restricted.join("\n"),
    allowance: budget ? amountText(budget.allowance, budget.exponent) : "", ceiling: budget ? amountText(budget.ceiling, budget.exponent) : "",
    cpus: String(draft.resources.cpus), memory_mib: String(draft.resources.memory_mib), disk_gib: String(draft.resources.disk_gib),
    credentials: draft.credentials.join("\n"), task: draft.task ?? "",
  };
}
export const sameFields = (a: Fields, b: Fields) => (Object.keys(a) as (keyof Fields)[]).every(key => a[key] === b[key]);

const lines = (text: string) => text.split("\n").map(line => line.trim()).filter(Boolean);
const readCount = (text: string) => { const value = text.trim(); return /^[1-9]\d*$/.test(value) && Number(value) <= U32_MAX ? Number(value) : null; };

/**
 * One validation for Save draft and Approve. Both need valid numbers, so a saved draft is always approvable.
 * A zero allowance or ceiling is valid: it allows no spending. An empty restricted list is valid: it grants no actions.
 * The server owns resource limits. The client checks only the u32 domain.
 */
export function validate(fields: Fields, base: SetupDraft, approve: boolean): { errors: Errors; draft: SetupDraft | null } {
  const errors: Errors = {};
  if (!fields.name.trim()) errors.name = "Enter a name.";
  if (!fields.instructions.trim()) errors.instructions = "Enter the job instructions.";
  if (approve && !fields.model.trim()) errors.model = "Enter a model before approval.";
  const resources = {} as Record<ResourceKey, number>;
  for (const [key, label] of [["cpus", "CPUs"], ["memory_mib", "Memory"], ["disk_gib", "Disk"]] as const) {
    const value = readCount(fields[key]);
    if (value === null) errors[key] = `${label}: enter a whole number from 1 to ${U32_MAX}.`; else resources[key] = value;
  }
  const credentials = lines(fields.credentials);
  if (new Set(credentials).size !== credentials.length) errors.credentials = "Each credential label must be different.";
  let budget = base.budget;
  if (budget) {
    const allowance = readAmount(fields.allowance, budget.exponent), ceiling = readAmount(fields.ceiling, budget.exponent);
    const hint = `a number in ${budget.currency} with at most ${budget.exponent} decimal places`;
    if (allowance === null) errors.allowance = `Allowance: enter ${hint}.`;
    if (ceiling === null) errors.ceiling = `Ceiling: enter ${hint}.`;
    if (allowance !== null && ceiling !== null && allowance > ceiling) errors.allowance = "The allowance must be at or below the ceiling.";
    if (allowance !== null && ceiling !== null) budget = { ...budget, allowance, ceiling };
  } else if (approve) errors.budget = "The server has no configured spending budget. Setup cannot be approved.";
  if (Object.keys(errors).length) return { errors, draft: null };
  return { errors, draft: { ...base, name: fields.name.trim(), instructions: fields.instructions, model: fields.model.trim(), authority: fields.restricted ? { Restricted: lines(fields.allowed) } : "Full", resources, budget, credentials, task: fields.task.trim() ? fields.task : null } };
}
export const firstInvalid = (errors: Errors) => FIELD_ORDER.find(key => errors[key]);
