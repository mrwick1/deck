import { useCallback, useEffect, useRef, useState } from "react";
import type { SetupRecord } from "../lib/api";
import { hasUnsaved, receiveRow, without, type Entries, type Entry } from "./setup-editor";

/**
 * Unsaved review editors, kept above the router so leaving the screen keeps the edits.
 * Polled rows update the store here. Nothing is written to storage, so no secret persists.
 */
export function useSetupEditors(rows: SetupRecord[]) {
  const [entries, setEntries] = useState<Entries>({});
  const guard = useRef(new Set<string>());
  useEffect(() => { setEntries(previous => rows.reduce(receiveRow, previous)); }, [rows]);
  const patch = useCallback((id: string, change: (entry: Entry) => Entry) => setEntries(previous => previous[id] ? { ...previous, [id]: change(previous[id]) } : previous), []);
  const discard = useCallback((id: string) => setEntries(previous => without(previous, id)), []);
  const unsaved = Object.values(entries).some(hasUnsaved);
  useEffect(() => {
    if (!unsaved) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);
  return { entries, patch, discard, guard: guard.current };
}
