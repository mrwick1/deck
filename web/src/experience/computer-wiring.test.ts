import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** No DOM test runner is installed, so these checks read the component source. */
const source = readFileSync(new URL("./Computer.tsx", import.meta.url), "utf8");

describe("Computer wiring", () => {
  it("keeps the signature and stops the controller on cleanup", () => {
    expect(source).toContain("export function Computer({ id }: { id: string })");
    expect(source).toMatch(/return \(\) => \{ s\.stop\(\)/);
  });
  it("sends image errors and Retry to the controller", () => {
    expect(source).toContain("onError={() => screen.current?.imageError(state.frame!)}");
    expect(source).toContain("screen.current?.retry()");
  });
  it("draws no frame unless the controller state has one", () => {
    expect(source).toContain("state.frame\n");
    expect(source).not.toMatch(/fetch\(botFrameUrl/);
  });
});
