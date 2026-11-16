// In-app navigation over hash routes. Each history entry stores its depth in `history.state`.
// Back leaves the app never: at depth 0 it replaces the entry with Home.
export type NavigationEnv = {
  history: { readonly state: unknown; pushState(state: unknown, title: string, url?: string | null): void; replaceState(state: unknown, title: string, url?: string | null): void; back(): void };
  location: { readonly hash: string };
  addEventListener(type: "popstate", listener: () => void): void;
  removeEventListener(type: "popstate", listener: () => void): void;
};

const depthOf = (state: unknown) => state && typeof state === "object" && typeof (state as { deckDepth?: unknown }).deckDepth === "number" ? (state as { deckDepth: number }).deckDepth : null;

export function createNavigation(env: NavigationEnv) {
  const route = () => env.location.hash.slice(1) || "home";
  const listeners = new Set<(route: string) => void>();
  const notify = () => listeners.forEach(listener => listener(route()));
  let depth = depthOf(env.history.state) ?? 0;
  if (depthOf(env.history.state) === null) env.history.replaceState({ deckDepth: 0 }, "");
  const onPop = () => {
    const stored = depthOf(env.history.state);
    if (stored === null) { depth += 1; env.history.replaceState({ deckDepth: depth }, ""); } else depth = stored;
    notify();
  };
  env.addEventListener("popstate", onPop);
  return {
    route,
    go(next: string, { replace = false } = {}) {
      if (replace || next === route()) env.history.replaceState({ deckDepth: depth }, "", `#${next}`);
      else { depth += 1; env.history.pushState({ deckDepth: depth }, "", `#${next}`); }
      notify();
    },
    back() {
      if (depth > 0) env.history.back();
      else if (route() !== "home") { env.history.replaceState({ deckDepth: 0 }, "", "#home"); notify(); }
    },
    subscribe(listener: (route: string) => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    dispose() { env.removeEventListener("popstate", onPop); },
  };
}
