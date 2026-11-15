/** Live screen controller. It holds no React state, so tests can drive it with fakes. */
export type Phase = "waiting" | "loading" | "live" | "hidden" | "reconnecting" | "recovering" | "failed" | "stopped";
export interface ScreenState { frame: string | null; phase: Phase; message: string }
export interface StreamLike {
  readyState: number;
  onerror: (() => void) | null;
  addEventListener(type: "frame", fn: (event: { data: string }) => void): void;
  close(): void;
}
export interface Deps {
  openStream(): StreamLike;
  frameUrl(seq: number): string;
  fetch(url: string, init: { signal: AbortSignal; cache: "no-store" }): Promise<Response>;
  createObjectURL(blob: Blob): string;
  revokeObjectURL(url: string): void;
  preload(url: string, ok: () => void, fail: () => void): () => void;
}

/** Waits before each retry of the latest frame. After the last one the viewer must press Retry. */
export const RETRY_DELAYS = [500, 1000, 2000];

export function createScreen(deps: Deps, emit: (s: ScreenState) => void) {
  let closed = false, version = 0, seq: number | null = null, attempt = 0;
  let url: string | null = null, pending: string | null = null;
  let controller: AbortController | undefined, timer: ReturnType<typeof setTimeout> | undefined, cancelPreload: (() => void) | undefined;
  let stream: StreamLike;
  const set = (phase: Phase, message: string) => emit({ frame: url, phase, message });
  const revoke = (u: string | null) => { if (u) deps.revokeObjectURL(u); };
  /** Abort the work in flight and revoke every URL that is not shown. */
  const cancel = () => {
    controller?.abort(); controller = undefined;
    clearTimeout(timer); timer = undefined;
    cancelPreload?.(); cancelPreload = undefined;
    revoke(pending); pending = null;
  };
  const clear = () => { revoke(url); url = null; };
  const fail = (own: number) => {
    if (closed || own !== version) return;
    cancel(); clear();
    if (attempt < RETRY_DELAYS.length) {
      timer = setTimeout(() => { timer = undefined; load(own); }, RETRY_DELAYS[attempt++]);
      set("recovering", "Screen frame failed to load. Retrying…");
    } else set("failed", "Screen frame could not be loaded.");
  };
  const load = async (own: number) => {
    if (closed || own !== version || seq === null) return;
    if (!url) set("loading", "Loading the screen");
    const signal = (controller = new AbortController()).signal;
    try {
      const response = await deps.fetch(deps.frameUrl(seq), { signal, cache: "no-store" });
      if (closed || own !== version) return;
      if (response.status === 204) { cancel(); clear(); set("hidden", "Screen unavailable or hidden for privacy"); return; }
      if (!response.ok || !response.headers.get("content-type")?.startsWith("image/png")) throw new Error("Frame unavailable");
      const blob = await response.blob();
      if (closed || own !== version) return;
      if (!blob.size) throw new Error("Empty frame");
      const candidate = pending = deps.createObjectURL(blob);
      cancelPreload = deps.preload(candidate, () => {
        if (closed || own !== version || pending !== candidate) return;
        cancelPreload = undefined; pending = null; revoke(url); url = candidate; set("live", "");
      }, () => { if (!closed && own === version && pending === candidate) fail(own); });
    } catch { fail(own); }
  };
  const open = () => {
    const s = stream = deps.openStream();
    s.addEventListener("frame", event => {
      if (closed || s !== stream) return;
      let next: number;
      try { next = JSON.parse(event.data).seq; } catch { return; }
      ++version; cancel(); seq = next; attempt = 0;
      load(version);
    });
    s.onerror = () => {
      if (closed || s !== stream) return;
      ++version; cancel(); clear();
      if (s.readyState === 2) set("stopped", "Screen connection stopped.");
      else set("reconnecting", "Screen connection interrupted. Reconnecting…");
    };
  };
  open();
  return {
    /** Fetch the latest frame again, or open a stopped stream again. */
    retry() {
      if (closed) return;
      if (stream.readyState === 2) { stream.close(); seq = null; ++version; cancel(); clear(); open(); set("waiting", "Waiting for the first frame"); return; }
      if (seq === null) return;
      ++version; cancel(); attempt = 0; load(version);
    },
    /** The <img> failed to draw the frame that it shows. */
    imageError(u: string) { if (u === url) { ++version; fail(version); } },
    stop() { if (closed) return; closed = true; ++version; cancel(); clear(); stream.close(); },
  };
}
