export function linkLabel(href: string | undefined, text: string): string {
  if (!href) return text;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return text;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return text;
  const bare = (value: string) => value.replace(/\/$/, "");
  if (bare(text) !== bare(href)) return text;
  return url.hostname.replace(/^www\./, "");
}
