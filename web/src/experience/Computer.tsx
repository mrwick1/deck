import { useEffect, useState } from "react";
import { botFrameUrl, botScreenUrl } from "../lib/api";

/** Keep only confirmed PNG frames. Empty responses and disconnects clear the visible frame. */
export function Computer({ id }: { id: string }) {
  const [frame, setFrame] = useState<string | null>(null);
  const [message, setMessage] = useState("Waiting for the first frame");
  useEffect(() => {
    let closed = false, version = 0;
    let url: string | null = null;
    let controller: AbortController | undefined;
    const clear = () => { if (url) URL.revokeObjectURL(url); url = null; setFrame(null); };
    const es = new EventSource(botScreenUrl(id));
    es.addEventListener("frame", async event => {
      const own = ++version;
      controller?.abort(); controller = new AbortController();
      try {
        const { seq } = JSON.parse((event as MessageEvent).data);
        const response = await fetch(botFrameUrl(seq, id), { signal: controller.signal, cache: "no-store" });
        if (closed || own !== version) return;
        if (response.status === 204) { clear(); setMessage("Screen unavailable or hidden for privacy"); return; }
        if (!response.ok || !response.headers.get("content-type")?.startsWith("image/png")) throw new Error("Frame unavailable");
        const blob = await response.blob();
        if (closed || own !== version) return;
        clear(); url = URL.createObjectURL(blob); setFrame(url); setMessage("");
      } catch { if (!closed && own === version) { clear(); setMessage("Screen connection interrupted. Reconnecting…"); } }
    });
    es.onerror = () => { ++version; controller?.abort(); clear(); setMessage("Screen connection interrupted. Reconnecting…"); };
    return () => { closed = true; ++version; es.close(); controller?.abort(); if (url) URL.revokeObjectURL(url); };
  }, [id]);
  return <div className="live-screen">{frame ? <img src={frame} alt="Live screen of the bot computer"/> : <p role="status">{message}</p>}</div>;
}
