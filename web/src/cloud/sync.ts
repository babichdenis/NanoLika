/**
 * Chat sync, read side.
 *
 * The relay keeps one history per account: the same conversation answers in Telegram, in
 * MAX, in the browser and on the computer. This module only *reads* it for now — the
 * catalogue of conversations and their messages — so the cloud screen can show the real
 * history before anything is written back. Publishing is the next step, and it will use the
 * same shapes the phone apps and the old web client already send.
 */
import { cloudRequest } from "./client";

const DEVICE_KEY = "nl.web.chat-client.web";

/** This browser's stable name in the sync, the same convention the old client used. */
export function deviceId(): string {
  try {
    const saved = localStorage.getItem(DEVICE_KEY);
    if (saved) return saved;
    const made = "web-" + (crypto.randomUUID?.() ?? String(Date.now()));
    localStorage.setItem(DEVICE_KEY, made);
    return made;
  } catch {
    return "web-temporary";
  }
}

export interface ConversationMeta {
  cid: string;
  title?: string;
  archived?: boolean;
  unread?: number;
  updated_at?: number;
  device?: string;
  model?: string;
  reasoning?: string;
}

export interface SyncedAttachment {
  name?: string;
  mime?: string;
  size?: number;
  url?: string;
}

export interface SyncedMessage {
  mid: string;
  cid: string;
  seq: number;
  role: "user" | "assistant" | string;
  text: string;
  created_at: number;
  attachments?: SyncedAttachment[];
  truncated?: boolean;
}

export interface MessagePage {
  messages: SyncedMessage[];
  through_seq?: number;
  more?: boolean;
}

export interface SyncState {
  ok?: boolean;
  account_id?: string;
  conversations?: number;
  messages?: number;
  paused?: boolean;
}

export function state(): Promise<SyncState> {
  return cloudRequest<SyncState>("/v1/sync/state");
}

export async function catalog(): Promise<ConversationMeta[]> {
  const device = deviceId();
  const query = new URLSearchParams({ client: device, device });
  const data = await cloudRequest<{ conversations?: ConversationMeta[]; complete?: boolean }>(
    `/v1/sync/catalog?${query.toString()}`,
  );
  if (!data?.complete) throw new Error("The chat list is not available");
  return data.conversations ?? [];
}

export async function messages(
  cid: string,
  options: { after?: number; before?: number; limit?: number } = {},
): Promise<MessagePage> {
  const query = new URLSearchParams();
  if (options.after !== undefined) query.set("after", String(options.after));
  if (options.before !== undefined) query.set("before", String(options.before));
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return cloudRequest<MessagePage>(`/v1/sync/conversations/${encodeURIComponent(cid)}/messages${suffix}`);
}

export interface OutgoingChat {
  cid: string;
  kind?: "main" | "side";
  title?: string;
  created_at: number;
  updated_at: number;
  archived?: boolean;
  model?: string;
  reasoning?: string;
}

export interface OutgoingMessage {
  mid: string;
  cid: string;
  role: "user" | "assistant";
  text: string;
  created_at: number;
  attachments?: SyncedAttachment[];
}

export interface PublishResult {
  rejected?: Array<{ reason?: string; cid?: string; cid_main?: string }>;
}

/** Send one turn to the account's history: the chat's card and its new messages. */
export function publish(chat: OutgoingChat, messages: OutgoingMessage[]): Promise<PublishResult> {
  return cloudRequest<PublishResult>("/v1/sync/changes", {
    method: "POST",
    body: JSON.stringify({ device: deviceId(), conversations: [chat], messages }),
  });
}
