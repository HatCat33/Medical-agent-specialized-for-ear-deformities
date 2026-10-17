import type {
  Conversation,
  ConversationDetail,
  Message,
  SendMessageResponse,
  StreamMessageHandlers,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
const VISITOR_KEY = "medical-agent-visitor-id";

function createVisitorId(): string {
  if (typeof crypto.randomUUID === "function") {
    return `web-${crypto.randomUUID()}`;
  }

  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `web-${value}`;
}

function visitorId(): string {
  let value = localStorage.getItem(VISITOR_KEY);
  if (!value) {
    value = createVisitorId();
    localStorage.setItem(VISITOR_KEY, value);
  }
  return value;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Visitor-ID": visitorId(),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`request_failed_${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

interface StreamPayload {
  message?: Message | string;
  content?: string;
}

async function streamMessage(
  conversationId: string,
  content: string,
  handlers: StreamMessageHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Visitor-ID": visitorId(),
    },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream_request_failed_${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;

  function processEvent(block: string) {
    const lines = block.replaceAll("\r\n", "\n").split("\n");
    const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
    const dataText = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!event || !dataText) return;

    const payload = JSON.parse(dataText) as StreamPayload;
    if (event === "user_message" && typeof payload.message === "object") {
      handlers.onUserMessage(payload.message);
    } else if (event === "delta" && payload.content) {
      handlers.onDelta(payload.content);
    } else if (event === "done" && typeof payload.message === "object") {
      completed = true;
      handlers.onDone(payload.message);
    } else if (event === "error") {
      throw new Error(typeof payload.message === "string" ? payload.message : "stream_failed");
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      processEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) processEvent(buffer);
  if (!completed && !signal.aborted) throw new Error("stream_ended_early");
}

export const api = {
  listConversations: () => request<Conversation[]>("/conversations"),
  createConversation: () =>
    request<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  getConversation: (conversationId: string) =>
    request<ConversationDetail>(`/conversations/${conversationId}`),
  deleteConversation: (conversationId: string) =>
    request<void>(`/conversations/${conversationId}`, { method: "DELETE" }),
  sendMessage: (conversationId: string, content: string) =>
    request<SendMessageResponse>(`/conversations/${conversationId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  streamMessage,
};
