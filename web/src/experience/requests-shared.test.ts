import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { expect, it } from "vitest";
import { WaitPanel } from "../components/SetupWait";
import type { SetupRecord } from "../lib/api";

it("restores a pending challenge's disabled controls when its panel mounts again", () => {
  const pending = { rid: "request", pending: true, text: "Sending your answer…", error: false };
  const response = { guard: { busy: true }, getSnapshot: () => pending, subscribe: () => () => {}, set: () => {} };
  const row = { version: 1, setup: { id: "bot", draft: { name: "Bot" }, wait: { request_id: "request", kind: "PhoneApproval", expires_at: null } } } as SetupRecord;
  const props = { row, protectedInput: false, refresh: async () => {}, response };
  const html = renderToString(createElement(WaitPanel, props));
  expect(html.match(/aria-disabled="true"/g)).toHaveLength(2);
  expect(html).toContain("Sending your answer…");
});
