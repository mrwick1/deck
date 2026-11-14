import * as api from "../lib/api";
import { parseMinor } from "../lib/bot";

export function readAmount(text: string, precision: number): number | null {
  if (!Number.isInteger(precision) || precision < 0 || precision > 9) return null;
  const value = text.trim();
  if (!/^\d+(?:\.\d+)?$/.test(value) || (value.split(".")[1]?.length ?? 0) > precision) return null;
  return /^0+(?:\.0+)?$/.test(value) ? 0 : parseMinor(value, precision);
}

export async function saveReview(row: api.SetupRecord, draft: api.SetupDraft, approve: boolean) {
  await api.editSetup(row.setup.id, row.version, draft);
  if (!approve) return;
  const saved = (await api.listSetups()).find(item => item.setup.id === row.setup.id);
  if (!saved || saved.phase !== "Draft" || saved.version !== row.version + 1 || saved.setup.draft.revision !== row.setup.draft.revision + 1) {
    throw new Error("The setup changed. Reload and review it before approval.");
  }
  await api.approveSetup(saved.setup.id, saved.version, saved.setup.draft.revision);
}

export function destinationOptions(rows: api.SetupRecord[]) {
  return rows.filter(row => row.runtime_ready).map(row => ({ id: row.setup.id, chatId: row.chat_id, name: row.setup.draft.name }));
}
