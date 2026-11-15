import { describe, expect, it } from "vitest";
import { draftKey, flipFrom, isAwayFromBottom, mergeFailed, moveDraft, routeAction, sendShortcut } from "./chat-flow";

describe("draft handoff", () => {
  it("moves text typed during the first send to the created chat and clears the new-chat draft", () => {
    expect(moveDraft({ "chat/new": " more", home: "x" }, 42)).toEqual({ "chat/42": " more", home: "x" });
  });
  it("appends to a draft the created chat already has", () => {
    expect(moveDraft({ "chat/new": "b", "chat/42": "a" }, 42)).toEqual({ "chat/42": "a\n\nb" });
  });
  it("leaves the drafts alone when nothing was typed", () => {
    const d = { home: "x" };
    expect(moveDraft(d, 42)).toBe(d);
    expect(moveDraft({ "chat/new": "  " }, 42)).toEqual({});
  });
  it("names the draft of a chat", () => {
    expect(draftKey(null)).toBe("chat/new");
    expect(draftKey(5)).toBe("chat/5");
  });
});

describe("failed message", () => {
  it("restores into an empty draft", () => expect(mergeFailed("  ", "hello")).toBe("hello"));
  it("puts the failed text before a newer draft without losing it", () =>
    expect(mergeFailed("later", "hello")).toBe("hello\n\nlater"));
});

describe("sendShortcut", () => {
  const key = (o: object) => ({ key: "Enter", ctrlKey: false, metaKey: false, shiftKey: false, nativeEvent: { isComposing: false }, ...o });
  it("sends on Ctrl or Cmd Enter", () => {
    expect(sendShortcut(key({ ctrlKey: true }))).toBe(true);
    expect(sendShortcut(key({ metaKey: true }))).toBe(true);
  });
  it("keeps plain and shift Enter as a newline", () => {
    expect(sendShortcut(key({}))).toBe(false);
    expect(sendShortcut(key({ shiftKey: true }))).toBe(false);
  });
  it("ignores Enter that confirms an IME composition", () => {
    expect(sendShortcut(key({ ctrlKey: true, nativeEvent: { isComposing: true } }))).toBe(false);
    expect(sendShortcut(key({ ctrlKey: true, keyCode: 229 }))).toBe(false);
  });
});

describe("routeAction", () => {
  it("opens the chat in the route", () => {
    expect(routeAction({ routeId: 5, chatId: null, entered: false, held: false })).toEqual({ open: 5 });
    expect(routeAction({ routeId: 5, chatId: 5, entered: false, held: false })).toBe(null);
  });
  it("keeps the created chat while the route still says new", () => {
    expect(routeAction({ routeId: null, chatId: 42, entered: false, held: false })).toBe(null);
  });
  it("starts an empty chat when the user enters New conversation", () => {
    expect(routeAction({ routeId: null, chatId: 42, entered: true, held: false })).toEqual({ open: null });
  });
  it("keeps the session while a first send is held", () => {
    expect(routeAction({ routeId: null, chatId: null, entered: true, held: true })).toBe(null);
    expect(routeAction({ routeId: null, chatId: 42, entered: true, held: true })).toBe(null);
  });
});

describe("scroll", () => {
  it("shows Latest only when the user is away from the bottom", () => {
    expect(isAwayFromBottom({ scrollTop: 900, clientHeight: 100, scrollHeight: 1000 })).toBe(false);
    expect(isAwayFromBottom({ scrollTop: 880, clientHeight: 100, scrollHeight: 1000 })).toBe(false);
    expect(isAwayFromBottom({ scrollTop: 500, clientHeight: 100, scrollHeight: 1000 })).toBe(true);
  });
  it("measures the start of the handoff from the textarea to the bubble", () => {
    expect(flipFrom({ right: 300, bottom: 700 }, { right: 380, bottom: 600 })).toEqual({ x: -80, y: 100 });
  });
});
