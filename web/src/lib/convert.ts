import type { ThreadMessageLike } from "@assistant-ui/react";
import { fileUrl } from "./api";
import type { Item, RunView } from "./events";

type Part = Exclude<ThreadMessageLike["content"], string>[number];

function part(item: Exclude<Item, { kind: "user" }>): Part {
  switch (item.kind) {
    case "assistant":
      return { type: "text", text: item.text };
    case "reasoning":
      return {
        type: "reasoning",
        text: item.text,
        ...(item.startedAt !== undefined && {
          timing: { startedAt: item.startedAt, completedAt: item.endedAt },
        }),
      };
    case "step":
      return {
        type: "tool-call",
        toolCallId: item.id,
        toolName: item.name,
        args: (item.args && typeof item.args === "object"
          ? item.args
          : {}) as never,
        argsText: JSON.stringify(item.args ?? {}),
        result: item.done ? (item.summary ?? "") : undefined,
        isError: item.done ? !item.ok : undefined,
        timing:
          item.startedAt === undefined
            ? undefined
            : { startedAt: item.startedAt, completedAt: item.endedAt },
        approval: item.approval && {
          id: item.approval.id,
          prompt: item.approval.reason,
          approved: item.approval.approved,
        },
      };
    case "file":
      return item.mime.startsWith("image/")
        ? { type: "image", image: fileUrl(item.fileId), filename: item.name }
        : {
            type: "file",
            data: fileUrl(item.fileId),
            mimeType: item.mime,
            filename: item.name,
            sourceType: "url",
          };
  }
}

/**
 * Turn the visible items into thread messages. A user item is one message.
 * The items between two user items form one assistant message.
 * `run` sets the status of the last assistant message.
 */
export function toThreadMessages(
  items: Item[],
  run: RunView | null,
): ThreadMessageLike[] {
  const out: ThreadMessageLike[] = [];
  let parts: Part[] = [];
  const flush = () => {
    if (parts.length)
      out.push({ role: "assistant", id: `m${out.length}`, content: parts });
    parts = [];
  };
  for (const item of items) {
    if (item.kind === "user") {
      flush();
      out.push({
        role: "user",
        id: `m${out.length}`,
        content: [{ type: "text", text: item.text }],
      });
    } else {
      parts.push(part(item));
    }
  }
  flush();
  const last = out[out.length - 1];
  if (run && last?.role === "assistant" && run.status !== "running") {
    out[out.length - 1] = {
      ...last,
      status:
        run.status === "done"
          ? { type: "complete", reason: "stop" }
          : run.status === "cancelled"
            ? { type: "incomplete", reason: "cancelled" }
            : {
                type: "incomplete",
                reason: "error",
                error: run.error ?? "The run failed",
              },
    };
  }
  return out;
}
