import { afterEach, describe, expect, it, vi } from "vitest";

import { api, currentUserReference, formatUserReference } from "./api";
import type { Message } from "./types";

afterEach(() => vi.unstubAllGlobals());

describe("user reference", () => {
  it("matches the first 16 hexadecimal characters of the backend SHA-256 reference", async () => {
    const storage = new Map([["medical-agent-visitor-id", "web-test-visitor"]]);
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });

    const reference = await currentUserReference();

    expect(reference).toBe("ab3d6c568d61eebf");
    expect(formatUserReference(reference)).toBe("AB3D-6C56-8D61-EEBF");
  });
});

describe("stream message terminal event", () => {
  it("returns on the done event without waiting for the HTTP body to close", async () => {
    const storage = new Map([["medical-agent-visitor-id", "web-test-visitor"]]);
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
    });

    const message: Message = {
      id: "answer",
      conversation_id: "conversation",
      sequence_no: 2,
      role: "assistant",
      content: "回答完成。",
      status: "completed",
      safety_code: null,
      created_at: "2026-11-05T00:00:00Z",
      attachments: [],
    };
    const payload = new TextEncoder().encode(
      `event: done\ndata: ${JSON.stringify({ message })}\n\n`,
    );
    let bodyCancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(payload);
        // Deliberately keep the stream open to emulate a slow proxy close.
      },
      cancel() {
        bodyCancelled = true;
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));

    const completed: Message[] = [];
    await api.streamMessage(
      "conversation",
      "问题",
      [],
      "text",
      {
        onUserMessage: () => undefined,
        onDelta: () => undefined,
        onDone: (item) => completed.push(item),
      },
      new AbortController().signal,
    );

    expect(completed).toEqual([message]);
    expect(bodyCancelled).toBe(true);
  });
});
