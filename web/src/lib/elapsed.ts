// From T3 Code, apps/web/src/components/chat/AgentElapsed.tsx (MIT, see web/THIRD_PARTY.md).

/** A duration such as "45s", "2m 05s" or "1h 03m". */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  if (minutes === 0) return `${seconds}s`;
  const hours = Math.floor(minutes / 60);
  if (hours === 0)
    return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}
