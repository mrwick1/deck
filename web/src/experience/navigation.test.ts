import { describe, expect, it } from "vitest";
import { createNavigation, type NavigationEnv } from "./navigation";

// A small stand-in for the browser's session history. It keeps entries and an index,
// truncates forward entries on push, and fires popstate on traversal and on hash edits.
function fakeBrowser(hash = "") {
  const entries: { hash: string; state: unknown }[] = [{ hash, state: null }];
  let index = 0;
  const listeners: (() => void)[] = [];
  const fire = () => listeners.forEach(listener => listener());
  const env: NavigationEnv = {
    history: {
      get state() { return entries[index].state; },
      pushState(state, _title, url) { entries.splice(index + 1); entries.push({ state, hash: String(url ?? "") }); index++; },
      replaceState(state, _title, url) { entries[index] = { state, hash: url === undefined || url === null ? entries[index].hash : String(url) }; },
      back() { if (index > 0) { index--; fire(); } },
    },
    location: { get hash() { return entries[index].hash; } },
    addEventListener: (_type, listener) => { listeners.push(listener); },
    removeEventListener: (_type, listener) => { listeners.splice(listeners.indexOf(listener), 1); },
  };
  return {
    env, entries, get index() { return index; },
    forward() { if (index < entries.length - 1) { index++; fire(); } },
    typeHash(next: string) { entries.splice(index + 1); entries.push({ hash: `#${next}`, state: null }); index++; fire(); },
  };
}
const start = (hash = "") => {
  const browser = fakeBrowser(hash);
  const nav = createNavigation(browser.env);
  const seen: string[] = [];
  nav.subscribe(route => seen.push(route));
  return { ...browser, browser, nav, seen };
};

describe("in-app Back", () => {
  it("returns Home > Conversations > New conversation one step at a time", () => {
    const t = start();
    t.nav.go("history"); t.nav.go("chat/new");
    expect(t.nav.route()).toBe("chat/new");
    t.nav.back(); expect(t.nav.route()).toBe("history");
    t.nav.back(); expect(t.nav.route()).toBe("home");
    expect(t.browser.index).toBe(0);
    expect(t.browser.entries).toHaveLength(3);
  });

  it("replaces a direct entry with Home and never leaves the app", () => {
    const t = start("#history");
    t.nav.back();
    expect(t.nav.route()).toBe("home");
    expect(t.browser.entries).toHaveLength(1);
    expect(t.seen).toEqual(["home"]);
    t.nav.back();
    expect(t.nav.route()).toBe("home");
    expect(t.browser.entries).toHaveLength(1);
  });

  it("keeps depth across browser Forward", () => {
    const t = start();
    t.nav.go("history"); t.nav.back();
    t.browser.forward();
    expect(t.nav.route()).toBe("history");
    t.nav.back();
    expect(t.nav.route()).toBe("home");
    expect(t.browser.index).toBe(0);
  });

  it("keeps depth across a refresh", () => {
    const t = start();
    t.nav.go("history"); t.nav.go("setup/a");
    const reloaded = createNavigation(t.browser.env);
    expect(reloaded.route()).toBe("setup/a");
    reloaded.back();
    expect(reloaded.route()).toBe("history");
    expect(t.browser.index).toBe(1);
  });

  it("does not stack the same route twice", () => {
    const t = start();
    t.nav.go("history"); t.nav.go("history");
    expect(t.browser.entries).toHaveLength(2);
    t.nav.back();
    expect(t.nav.route()).toBe("home");
  });

  it("replaces an entry when asked, so Back skips the placeholder", () => {
    const t = start();
    t.nav.go("history"); t.nav.go("chat/new"); t.nav.go("chat/7", { replace: true });
    expect(t.browser.entries).toHaveLength(3);
    t.nav.back();
    expect(t.nav.route()).toBe("history");
  });

  it("counts a typed hash as one step deeper", () => {
    const t = start();
    t.typeHash("history");
    expect(t.nav.route()).toBe("history");
    t.nav.back();
    expect(t.nav.route()).toBe("home");
  });

  it("notifies once for each route change", () => {
    const t = start();
    t.nav.go("history"); t.nav.back();
    expect(t.seen).toEqual(["history", "home"]);
  });
});
