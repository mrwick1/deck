import { describe, expect, it } from "vitest";
import { linkLabel } from "./links";

describe("linkLabel", () => {
  it("shortens a raw URL to its host", () => {
    expect(
      linkLabel("https://example.com/news/2026/long-path", "https://example.com/news/2026/long-path"),
    ).toBe("example.com");
  });

  it("strips a leading www. and ignores a trailing slash", () => {
    expect(linkLabel("https://www.example.com/", "https://www.example.com")).toBe(
      "example.com",
    );
  });

  it("keeps the text when it differs from the href", () => {
    expect(linkLabel("https://example.com/a", "Read the story")).toBe(
      "Read the story",
    );
  });

  it("keeps the text for a mailto: link", () => {
    expect(linkLabel("mailto:user@example.com", "mailto:user@example.com")).toBe(
      "mailto:user@example.com",
    );
  });

  it("keeps the text when the href is not a valid URL", () => {
    expect(linkLabel("not a url", "not a url")).toBe("not a url");
  });
});
