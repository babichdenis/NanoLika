import { describe, expect, it } from "vitest";
import { streamChat } from "./client";

function sse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("the relay's stream", () => {
  it("collects text, reasoning and usage, even when a line is split across chunks", async () => {
    const deltas: string[] = [];
    const thoughts: string[] = [];
    const response = sse([
      'data: {"choices":[{"delta":{"reasoning_content":"думаю"}}]}\n',
      '\ndata: {"choices":[{"delta":{"content":"При"}}]}\n\ndata: {"choices":[{"delta":{"cont',
      'ent":"вет"}}]}\n\n',
      'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n',
      "data: [DONE]\n\n",
    ]);
    const result = await streamChat(response, {
      onDelta: (text) => deltas.push(text),
      onReasoning: (text) => thoughts.push(text),
    });
    expect(result.text).toBe("Привет");
    expect(result.reasoning).toBe("думаю");
    expect(result.usage).toEqual({ prompt_tokens: 7, completion_tokens: 2 });
    expect(deltas).toEqual(["При", "вет"]);
    expect(thoughts).toEqual(["думаю"]);
  });

  it("ignores keep-alive lines and a broken chunk instead of failing the turn", async () => {
    const response = sse([": ping\n\n", "data: {not json}\n\n", 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n']);
    const result = await streamChat(response);
    expect(result.text).toBe("ok");
  });
});
