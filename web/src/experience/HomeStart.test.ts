import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HomeStart } from "./HomeStart";

const base = { draft: "", setDraft: () => {}, submit: () => {}, disabled: false, actions: [] };

describe("first-time Home markup", () => {
  it("places lost-reply recovery inside the scrolling main region", () => {
    const recovery = createElement("section", { className: "uncertain-create" }, "Check workspace");
    const markup = renderToString(createElement(HomeStart, { ...base, recovery }));
    const start = markup.indexOf('class="start-main"');
    const found = markup.indexOf("uncertain-create");
    const close = markup.indexOf("</main>");
    expect(start).toBeGreaterThan(-1);
    expect(found).toBeGreaterThan(start);
    expect(found).toBeLessThan(close);
  });

  it("renders no recovery region when none is passed", () => {
    expect(renderToString(createElement(HomeStart, base))).not.toContain("uncertain-create");
  });
});
