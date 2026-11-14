import { describe, expect, it } from "vitest";
import { journey, type Phase, type Action } from "./journey";
describe("experience journey", () => {
  it("requires review and owner help before work can finish", () => {
    let phase: Phase = "idle";
    for (const action of ["propose", "create", "connect", "approve", "finish"] as Action[]) phase = journey(phase, action);
    expect(phase).toBe("done");
    expect(journey("review", "finish")).toBe("review");
    expect(journey("phone", "finish")).toBe("phone");
  });
  it("holds paused or failed work until explicitly resumed", () => {
    expect(journey("paused", "finish")).toBe("paused");
    expect(journey("failed", "finish")).toBe("failed");
    expect(journey("paused", "resume")).toBe("working");
    expect(journey("failed", "retry")).toBe("working");
  });
});
