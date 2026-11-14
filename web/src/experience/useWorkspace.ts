import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api";

export type Workspace = {
  rows: api.SetupRecord[];
  chats: api.Chat[];
  bots: api.BotStatus[];
  enabled: boolean;
  protectedInput: boolean;
};
const empty: Workspace = { rows: [], chats: [], bots: [], enabled: false, protectedInput: false };

/** Serial overview reads. A failed refresh retains data and marks all controls stale. */
export function useWorkspace() {
  const [data, setData] = useState(empty);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(false);
  const pending = useRef<Promise<void> | null>(null);
  const refresh = useCallback(() => {
    if (pending.current) return pending.current;
    const request = (async () => {
      try {
        const [options, rows, chats, legacy] = await Promise.all([
          api.setupOptions(), api.listSetups(), api.listChats(),
          api.getBot().catch(e => { if (e instanceof api.ApiError && e.status === 404) return null; throw e; }),
        ]);
        const bots = await Promise.all(rows.filter(row => row.runtime_ready && row.setup.id !== legacy?.bot_id).map(row => api.getBot(row.setup.id)));
        if (legacy) bots.unshift(legacy);
        if (mounted.current) { setData({ rows, chats, bots, enabled: options.enabled, protectedInput: options.protected_input === true }); setError(null); }
      } catch (e) {
        if (mounted.current) setError(e instanceof api.ApiError && (e.status === 401 || e.status === 403) ? "Sign in to Deck to continue." : "Cannot refresh Deck. Showing the last received state.");
      } finally { if (mounted.current) setLoaded(true); pending.current = null; }
    })();
    pending.current = request;
    return request;
  }, []);
  useEffect(() => {
    mounted.current = true;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;
    const run = async () => { await refresh(); if (!closed) timer = setTimeout(run, 5000); };
    void run();
    return () => { mounted.current = false; closed = true; clearTimeout(timer); };
  }, [refresh]);
  return { ...data, loaded, error, refresh };
}
