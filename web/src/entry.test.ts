import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import config from "../vite.config";

const web = path.resolve(import.meta.dirname, "..");
const read = (file: string) => readFileSync(path.join(web, file), "utf8");

describe("the compact UI is the root app", () => {
  it("loads the compact UI from index.html", () => {
    expect(read("index.html")).toContain('src="/src/experience/main.tsx"');
  });
  it("builds no second app for the legacy experience page", () => {
    expect(existsSync(path.join(web, "experience.html"))).toBe(false);
    const input = (config.build?.rollupOptions?.input ?? {}) as Record<string, string>;
    expect(Object.keys(input).sort()).toEqual(["app", "prototype"]);
  });
  it("registers the service worker from the compact UI", () => {
    expect(read("src/experience/main.tsx")).toContain("registerSW");
  });
  it("sends navigations of the API and the sample page to the network, not the root", () => {
    const text = (read("vite.config.ts").match(/navigateFallbackDenylist: \[(.*)\]/) ?? [])[1] ?? "";
    const denylist = new Function(`return [${text}]`)() as RegExp[];
    const denied = (url: string) => denylist.some((re) => re.test(url));
    for (const url of ["/api/chats", "/prototype.html", "/experience.html"]) expect(denied(url), url).toBe(true);
    for (const url of ["/", "/index.html", "/chats/1"]) expect(denied(url), url).toBe(false);
  });
});
