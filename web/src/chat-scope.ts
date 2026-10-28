import type { ThreadMeta } from "./types";
export type ChatScope = "active" | "archive" | "local";
export function inChatScope(thread: ThreadMeta, scope: ChatScope): boolean {
  if (scope === "archive") return thread.archived === true;
  if (thread.archived) return false;
  return scope === "local" ? thread.local_only === true : thread.local_only !== true;
}
