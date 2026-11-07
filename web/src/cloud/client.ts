/**
 * The relay, as the browser sees it.
 *
 * The runtime client talks to its own `/api`; the cloud client talks to `cloud.nanolika.ru`
 * with an account key. This module is that second transport and nothing else: no store, no
 * UI. It speaks the same OpenAI-compatible shapes the phone apps and the old web client use,
 * so a message sent here lands in the same conversation as everywhere else.
 */

import { t } from "../i18n";

const TOKEN_KEY = "nm.key";
const SESSION_KEY = "nl.web.session.v1";
const RELAY_KEY = "nl.cloud.relay";

export interface CloudModel {
  id: string;
  name?: string;
  nanomuse?: {
    kind?: string;
    vision?: boolean;
    context_length?: number;
    price_cny?: { per_m_input?: number; per_m_output?: number };
  };
  architecture?: { input_modalities?: string[] };
}

export interface CloudPart {
  type: "text" | "image_url";
  text?: string;
  image_url?: { url: string };
}

export interface CloudMessage {
  role: "system" | "user" | "assistant";
  content: string | CloudPart[];
}

export interface CloudUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

/** Where the relay is: the same origin when the app is served from it, else the parameter. */
export function relayBase(): string {
  const fromQuery = new URLSearchParams(location.search).get("relay");
  if (fromQuery) {
    try {
      const url = new URL(fromQuery);
      if (url.protocol === "https:" || url.hostname === "localhost" || url.hostname === "127.0.0.1") {
        localStorage.setItem(RELAY_KEY, url.origin);
        return url.origin;
      }
    } catch {
      /* a broken parameter falls through to the stored one */
    }
  }
  const stored = localStorage.getItem(RELAY_KEY);
  if (stored) return stored;
  if (location.hostname.endsWith("nanolika.ru")) return location.origin;
  return "https://cloud.nanolika.ru";
}

export function cloudToken(): string {
  try {
    const session = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    if (session?.key) return String(session.key);
  } catch {
    /* fall through to the plain key */
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setCloudToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

/** A JSON object from the wire, without trusting it. */
export type Json = Record<string, unknown>;

export function isRecord(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorOf(data: unknown): Json {
  return isRecord(data) && isRecord(data.error) ? data.error : {};
}

export class CloudError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code = "",
    readonly extra: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "CloudError";
  }
}

export async function cloudRequest<T>(path: string, init: RequestInit = {}, timeout = 30_000): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const token = cloudToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  let response: Response;
  try {
    response = await fetch(relayBase() + path, { ...init, headers, signal: init.signal ?? controller.signal });
  } catch {
    throw new CloudError(t("Cannot reach the cloud. Check the connection and try again."), 0, "offline");
  } finally {
    clearTimeout(timer);
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = errorOf(data);
    throw new CloudError(String(error.message || t("The cloud refused the request.")), response.status, String(error.code || ""), error);
  }
  return data as T;
}

export async function models(): Promise<CloudModel[]> {
  const data = await cloudRequest<{ data?: CloudModel[] }>("/v1/models");
  return (data.data ?? []).filter((model) => (model.nanomuse?.kind ?? "chat") === "chat");
}

export function me(): Promise<Record<string, unknown>> {
  return cloudRequest("/v1/me");
}

/** One streamed turn. Returns the text the model wrote and the usage it reported. */
export async function chat(
  messages: CloudMessage[],
  model: string,
  options: {
    signal?: AbortSignal;
    reasoningEffort?: string;
    maxTokens?: number;
    onDelta?: (text: string) => void;
    onReasoning?: (text: string) => void;
  } = {},
): Promise<{ text: string; reasoning: string; usage: CloudUsage | null }> {
  const response = await fetch(relayBase() + "/v1/chat/completions", {
    method: "POST",
    cache: "no-store",
    signal: options.signal,
    headers: { Authorization: `Bearer ${cloudToken()}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      messages,
      stream: true,
      store: false,
      max_tokens: options.maxTokens ?? 4096,
      ...(options.reasoningEffort ? { reasoning_effort: options.reasoningEffort } : {}),
    }),
  });
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    const error = errorOf(data);
    throw new CloudError(String(error.message || t("The cloud refused the request.")), response.status, String(error.code || ""), error);
  }
  return streamChat(response, options);
}

/** Read one SSE reply: the deltas, the reasoning and the usage the relay reports. */
export async function streamChat(
  response: Response,
  options: { onDelta?: (text: string) => void; onReasoning?: (text: string) => void } = {},
): Promise<{ text: string; reasoning: string; usage: CloudUsage | null }> {
  if (!response.body) throw new CloudError(t("The cloud sent no stream."), 502, "no_stream");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let text = "";
  let reasoning = "";
  let usage: CloudUsage | null = null;

  const handle = (line: string) => {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) return;
    const payload = trimmed.slice(5).trim();
    if (!payload || payload === "[DONE]") return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      return;
    }
    if (!isRecord(parsed)) return;
    if (isRecord(parsed.usage)) usage = parsed.usage as CloudUsage;
    const choices = Array.isArray(parsed.choices) ? parsed.choices : [];
    const first = choices[0];
    const delta = isRecord(first) && isRecord(first.delta) ? first.delta : {};
    if (typeof delta.reasoning_content === "string" && delta.reasoning_content) {
      reasoning += delta.reasoning_content;
      options.onReasoning?.(delta.reasoning_content);
    }
    if (typeof delta.content === "string" && delta.content) {
      text += delta.content;
      options.onDelta?.(delta.content);
    }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) handle(line);
  }
  if (buffer) handle(buffer);
  return { text, reasoning, usage };
}
