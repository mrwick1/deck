import { useEffect, useRef, useState } from "react";
import { botFrameUrl, botScreenUrl } from "../lib/api";
import { createScreen, type ScreenState, type StreamLike } from "./computer-controller";
import "./computer.css";

const first: ScreenState = { frame: null, phase: "waiting", message: "Waiting for the first frame" };

/** Show the bot screen. The controller owns streams, retries and URLs; this file only draws its state. */
export function Computer({ id }: { id: string }) {
  const [state, setState] = useState(first);
  const frame = useRef<HTMLDivElement>(null);
  const screen = useRef<ReturnType<typeof createScreen>>(undefined);
  useEffect(() => {
    setState(first);
    const s = createScreen({
      openStream: () => new EventSource(botScreenUrl(id)) as unknown as StreamLike,
      frameUrl: seq => botFrameUrl(seq, id),
      fetch: (url, init) => fetch(url, init),
      createObjectURL: blob => URL.createObjectURL(blob),
      revokeObjectURL: url => URL.revokeObjectURL(url),
      preload: (url, ok, fail) => {
        const image = new Image();
        image.onload = ok; image.onerror = fail; image.src = url;
        return () => { image.onload = image.onerror = null; image.src = ""; };
      },
    }, setState);
    screen.current = s;
    return () => { s.stop(); screen.current = undefined; };
  }, [id]);
  const retry = state.phase === "failed" || state.phase === "stopped";
  return (
    <div className="computer-frame" ref={frame} tabIndex={-1} aria-label="Computer screen">
      <p role="status" className={state.frame ? "sr-only" : undefined}>{state.message}</p>
      {state.frame
        ? <img src={state.frame} alt="Live screen of the bot computer" onError={() => screen.current?.imageError(state.frame!)}/>
        : <>
            {retry && <button type="button" className="computer-retry" onClick={() => { screen.current?.retry(); frame.current?.focus({ preventScroll: true }); }}>{state.phase === "stopped" ? "Reconnect" : "Retry"}</button>}
          </>}
    </div>
  );
}
