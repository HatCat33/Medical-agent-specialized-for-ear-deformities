import { useCallback, useEffect, useRef, useState } from "react";

import { ChatPanel } from "../features/chat/ChatPanel";
import { HistorySidebar } from "../features/history/HistorySidebar";
import { api } from "../lib/api";
import type { Conversation, Message } from "../lib/types";

export function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const streamController = useRef<AbortController | null>(null);

  const refreshConversations = useCallback(async () => {
    const items = await api.listConversations();
    setConversations(items);
    return items;
  }, []);

  const openConversation = useCallback(async (conversationId: string) => {
    setError(null);
    setActiveId(conversationId);
    setHistoryOpen(false);
    const detail = await api.getConversation(conversationId);
    setMessages(detail.messages);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const items = await refreshConversations();
        if (items[0]) await openConversation(items[0].id);
      } catch {
        setError("暂时无法连接服务，请稍后重试。");
      }
    })();
  }, [openConversation, refreshConversations]);

  async function createConversation(): Promise<Conversation> {
    const conversation = await api.createConversation();
    setConversations((current) => [conversation, ...current]);
    setActiveId(conversation.id);
    setMessages([]);
    setHistoryOpen(false);
    return conversation;
  }

  async function deleteConversation(conversationId: string) {
    if (!window.confirm("确定删除这条会话及其中的消息吗？")) return;
    try {
      await api.deleteConversation(conversationId);
      const remaining = conversations.filter((item) => item.id !== conversationId);
      setConversations(remaining);
      if (activeId === conversationId) {
        if (remaining[0]) await openConversation(remaining[0].id);
        else {
          setActiveId(null);
          setMessages([]);
        }
      }
    } catch {
      setError("会话删除失败，请稍后重试。");
    }
  }

  async function submitMessage() {
    const content = input.trim();
    if (!content || sending) return;
    setInput("");
    setSending(true);
    setError(null);
    const controller = new AbortController();
    streamController.current = controller;
    try {
      const conversation = activeId
        ? conversations.find((item) => item.id === activeId) ?? (await createConversation())
        : await createConversation();
      const now = new Date().toISOString();
      const sequence = (messages.at(-1)?.sequence_no ?? 0) + 1;
      const userTemporaryId = `local-user-${Date.now()}`;
      const assistantTemporaryId = `local-assistant-${Date.now()}`;
      const userMessage: Message = {
        id: userTemporaryId,
        conversation_id: conversation.id,
        sequence_no: sequence,
        role: "user",
        content,
        status: "completed",
        safety_code: null,
        created_at: now,
      };
      const assistantMessage: Message = {
        id: assistantTemporaryId,
        conversation_id: conversation.id,
        sequence_no: sequence + 1,
        role: "assistant",
        content: "",
        status: "streaming",
        safety_code: null,
        created_at: now,
      };
      setMessages((current) => [...current, userMessage, assistantMessage]);
      setActiveId(conversation.id);

      await api.streamMessage(
        conversation.id,
        content,
        {
          onUserMessage: (message) => {
            setMessages((current) =>
              current.map((item) => (item.id === userTemporaryId ? message : item)),
            );
          },
          onDelta: (chunk) => {
            setMessages((current) =>
              current.map((item) =>
                item.id === assistantTemporaryId
                  ? { ...item, content: item.content + chunk }
                  : item,
              ),
            );
          },
          onDone: (message) => {
            setMessages((current) =>
              current.map((item) => (item.id === assistantTemporaryId ? message : item)),
            );
          },
        },
        controller.signal,
      );
    } catch (caught) {
      const stopped = caught instanceof DOMException && caught.name === "AbortError";
      setMessages((current) =>
        current.map((item) => {
          if (item.status !== "streaming") return item;
          return {
            ...item,
            content: item.content || (stopped ? "已停止生成。" : "回答生成失败，请稍后重试。"),
            status: "interrupted",
          };
        }),
      );
      if (!stopped) setError("连接中断，已保留当前消息，请稍后重试。");
    } finally {
      streamController.current = null;
      setSending(false);
      try {
        await refreshConversations();
      } catch {
        // The current message remains usable even if refreshing the sidebar fails.
      }
    }
  }

  function stopGeneration() {
    streamController.current?.abort();
  }

  const active = conversations.find((item) => item.id === activeId);

  return (
    <main className="app-shell">
      <div
        className={`sidebar-backdrop ${historyOpen ? "is-open" : ""}`}
        onClick={() => setHistoryOpen(false)}
        aria-hidden="true"
      />
      <HistorySidebar
        conversations={conversations}
        activeId={activeId}
        open={historyOpen}
        busy={sending}
        onClose={() => setHistoryOpen(false)}
        onCreate={() => void createConversation()}
        onSelect={(conversationId) => void openConversation(conversationId)}
        onDelete={(conversationId) => void deleteConversation(conversationId)}
      />
      <ChatPanel
        title={active?.title ?? "新会话"}
        messages={messages}
        value={input}
        sending={sending}
        error={error}
        onValueChange={setInput}
        onSubmit={() => void submitMessage()}
        onStop={stopGeneration}
        onOpenHistory={() => setHistoryOpen(true)}
        onSuggestion={setInput}
      />
    </main>
  );
}

