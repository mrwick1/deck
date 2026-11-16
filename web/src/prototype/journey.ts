export type Phase = "idle" | "review" | "connect" | "phone" | "working" | "paused" | "failed" | "done";
export type Action = "propose" | "create" | "connect" | "approve" | "pause" | "resume" | "fail" | "retry" | "finish" | "reset";
const transitions: Partial<Record<Phase, Partial<Record<Action, Phase>>>> = {
  idle: { propose: "review" }, review: { create: "connect" },
  connect: { connect: "phone" }, phone: { approve: "working" },
  working: { pause: "paused", fail: "failed", finish: "done" },
  paused: { resume: "working" }, failed: { retry: "working" },
};
export function journey(phase: Phase, action: Action): Phase {
  return action === "reset" ? "idle" : transitions[phase]?.[action] ?? phase;
}
export const labels: Record<Phase, string> = {
  idle: "Ready when you are", review: "Review your bot", connect: "Connect your account",
  phone: "Waiting for your approval", working: "Preparing your morning brief",
  paused: "Work paused", failed: "Connection interrupted", done: "Your morning brief is ready",
};
