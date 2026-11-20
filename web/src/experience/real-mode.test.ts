import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(import.meta.dirname, "..");
const files = (dir: string): string[] => readdirSync(dir).flatMap(name => {
  const full = path.join(dir, name);
  return statSync(full).isDirectory() ? files(full) : /\.tsx?$/.test(name) && !/\.test\./.test(name) ? [full] : [];
});

describe("real mode", () => {
  const live = [...files(path.join(root, "experience")), ...files(path.join(root, "lib")), ...files(path.join(root, "components"))];
  it("never imports the sample prototype", () => {
    for (const file of live) expect(readFileSync(file, "utf8"), file).not.toMatch(/from ["'](\.\.?\/)+prototype\//);
  });
  it("never renders model or mail text as HTML", () => {
    for (const file of files(root)) expect(readFileSync(file, "utf8"), file).not.toMatch(/dangerouslySetInnerHTML|\.innerHTML\s*=/);
  });
  it("the Gmail panel shows no sample fallback when status is absent", () => {
    expect(readFileSync(path.join(root, "experience/GmailHealth.tsx"), "utf8")).toMatch(/if \(!status\.gmail\) return null/);
  });
});
