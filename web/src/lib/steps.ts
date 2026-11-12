/** A short label for a step, such as "Opening example.com". */
export function describeStep(
  name: string,
  args: unknown,
  running: boolean,
): string {
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
  const verb = (now: string, past: string, rest = "") =>
    `${running ? now : past}${rest ? " " + rest : ""}`;

  switch (name.replace(/^browser_/, "")) {
    case "open":
      return verb("Opening", "Opened", host(str("url")));
    case "act": {
      const op = str("op");
      if (op === "goto") return verb("Going to", "Went to", host(str("url")));
      if (op === "type") return verb("Typing", "Typed", quote(str("text")));
      if (op === "press") return verb("Pressing", "Pressed", str("key"));
      if (op === "scroll") return verb("Scrolling", "Scrolled");
      if (op === "wait") return verb("Waiting", "Waited");
      if (op === "reload") return verb("Reloading", "Reloaded");
      return verb("Clicking", "Clicked");
    }
    case "step":
      return verb("Running a step", "Ran a step");
    case "snapshot":
    case "read":
      return verb("Reading the page", "Read the page");
    case "find":
      return verb(
        "Looking for",
        "Looked for",
        quote(str("text") || str("query") || str("loc")),
      );
    case "links":
      return verb("Listing links", "Listed links");
    case "screenshot":
      return verb("Taking a screenshot", "Took a screenshot");
    case "eval":
      return verb("Running a script", "Ran a script");
    case "ensure_session":
      return verb("Signing in", "Signed in");
    case "save_state":
      return verb("Saving the session", "Saved the session");
    case "close":
      return verb("Closing the browser", "Closed the browser");
    case "send_file":
      return verb("Sending a file", "Sent a file");
    default:
      return verb("Running", "Ran", name);
  }
}
