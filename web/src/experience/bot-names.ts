const key = (name: string) => name.trim().toLowerCase();

/** Names that occur more than once. Compared trimmed and case-insensitive. Returns the lower-case keys. */
export function duplicateNames(items: { name: string }[]): Set<string> {
  const seen = new Set<string>();
  const dupes = new Set<string>();
  for (const { name } of items) {
    const k = key(name);
    if (seen.has(k)) dupes.add(k);
    else seen.add(k);
  }
  return dupes;
}

/** The last 6 characters of a bot id. A shorter id returns whole. */
export const shortId = (id: string) => id.slice(-6);
