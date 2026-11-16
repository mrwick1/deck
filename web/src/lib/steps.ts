type Mode = "now" | "past" | "base";

/** A short label for a step, such as "Opening example.com". */
export function describeStep(
  name: string,
  args: unknown,
  running: boolean,
): string {
  return stepLabel(name, args, running ? "now" : "past");
}

/** The imperative label for a step that waits for the owner, such as "Open example.com". */
export function describePendingStep(name: string, args: unknown): string {
  return stepLabel(name, args, "base");
}

function stepLabel(name: string, args: unknown, mode: Mode): string {
  const a = (args && typeof args === "object" ? args : {}) as Record<
    string,
    unknown
  >;
  const str = (k: string) => (typeof a[k] === "string" ? (a[k] as string) : "");
  const host = (u: string) => {
    try {
      return new URL(u).host || u;
    } catch {
      return u;
    }
  };
  const quote = (s: string) =>
    s.length > 40 ? `“${s.slice(0, 40)}…”` : `“${s}”`;
  const verb = (now: string, past: string, base: string, rest = "") =>
    `${mode === "now" ? now : mode === "past" ? past : base}${rest ? " " + rest : ""}`;

  switch (name.replace(/^browser_/, "")) {
    case "open":
      return verb("Opening", "Opened", "Open", host(str("url")));
    case "act": {
      const op = str("op");
      if (op === "goto")
        return verb("Going to", "Went to", "Go to", host(str("url")));
      if (op === "type")
        return verb("Typing", "Typed", "Type", quote(str("text")));
      if (op === "press") return verb("Pressing", "Pressed", "Press", str("key"));
      if (op === "scroll") return verb("Scrolling", "Scrolled", "Scroll");
      if (op === "wait") return verb("Waiting", "Waited", "Wait");
      if (op === "reload") return verb("Reloading", "Reloaded", "Reload");
      return verb("Clicking", "Clicked", "Click");
    }
    case "step":
      return verb("Running a step", "Ran a step", "Run a step");
    case "snapshot":
    case "read":
      return verb("Reading the page", "Read the page", "Read the page");
    case "find":
      return verb(
        "Looking for",
        "Looked for",
        "Look for",
        quote(str("text") || str("query") || str("loc")),
      );
    case "links":
      return verb("Listing links", "Listed links", "List links");
    case "screenshot":
      return verb(
        "Taking a screenshot",
        "Took a screenshot",
        "Take a screenshot",
      );
    case "eval":
      return verb("Running a script", "Ran a script", "Run a script");
    case "ensure_session":
      return verb("Signing in", "Signed in", "Sign in");
    case "save_state":
      return verb("Saving the session", "Saved the session", "Save the session");
    case "close":
      return verb("Closing the browser", "Closed the browser", "Close the browser");
    case "send_file":
      return verb("Sending a file", "Sent a file", "Send a file");
    default:
      if (mode === "base") {
        const words = name.replace(/^browser_/, "").replace(/_/g, " ");
        return words.charAt(0).toUpperCase() + words.slice(1);
      }
      return verb("Running", "Ran", "", name);
  }
}
