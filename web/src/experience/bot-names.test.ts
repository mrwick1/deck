import { describe, expect, it } from "vitest";
import { duplicateNames, shortId } from "./bot-names";

describe("duplicateNames", () => {
  it("returns an empty set when no name repeats", () => {
    expect(duplicateNames([{ name: "Daily tech brief" }, { name: "Inbox sorter" }]).size).toBe(0);
  });

  it("returns the key of a repeated name", () => {
    expect(duplicateNames([{ name: "Daily tech brief" }, { name: "Daily tech brief" }])).toEqual(new Set(["daily tech brief"]));
  });

  it("treats case and surrounding spaces as the same name", () => {
    expect(duplicateNames([{ name: "Daily tech brief" }, { name: "  daily TECH brief " }])).toEqual(new Set(["daily tech brief"]));
  });
});

describe("shortId", () => {
  it("returns the last 6 characters of a long id", () => {
    expect(shortId("bot-0123456789abcdef")).toBe("abcdef");
  });

  it("returns a short id whole", () => {
    expect(shortId("bot-7")).toBe("bot-7");
  });
});
