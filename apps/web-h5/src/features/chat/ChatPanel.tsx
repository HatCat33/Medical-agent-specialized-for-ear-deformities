import { AlertCircle, Menu, Send, ShieldCheck, Sparkles, Square } from "lucide-react";
import { type FormEvent, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Message } from "../../lib/types";

interface ChatPanelProps {
  title: string;
  messages: Message[];
  value: string;
  sending: boolean;
  error: string | null;
  onValueChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  onOpenHistory: () => void;
  onSuggestion: (value: string) => void;
}

const suggestions = [
  "小耳畸形通常有哪些类型？",
  "第一次就诊前需要准备什么？",
  "日常护理有哪些注意事项？",
];

export function ChatPanel({
  title,
  messages,
  value,
  sending,
  error,
  onValueChange,
  onSubmit,
  onStop,
  onOpenHistory,
  onSuggestion,
}: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldStickToBottom = useRef(true);

  useEffect(() => {
    if (!shouldStickToBottom.current) return;
    const frame = requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, sending]);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 132)}px`;
  }, [value]);

  useEffect(() => {
    if (sending) shouldStickToBottom.current = true;
    if (!sending) textareaRef.current?.focus();
  }, [sending]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <section className="chat-shell">
      <header className="chat-header">
        <button
          className="icon-button history-trigger"
          type="button"
          onClick={onOpenHistory}
          title="历史会话"
        >
          <Menu size={20} />
        </button>
        <div className="brand-mark" aria-hidden="true">
          <Sparkles size={19} />
        </div>
        <div className="chat-heading">
          <strong>耳畸形科普助手</strong>
          <span>{title}</span>
        </div>
        <div className="scope-label">
          <ShieldCheck size={15} />
          仅限科普
        </div>
      </header>

      <div
        className="message-scroll"
        ref={scrollRef}
        aria-live="polite"
        onScroll={(event) => {
          const element = event.currentTarget;
          shouldStickToBottom.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 96;
        }}
      >
        {messages.length === 0 ? (
          <div className="welcome-block">
            <div className="assistant-avatar" aria-hidden="true">
              <Sparkles size={24} />
            </div>
            <h1>你好，我是耳畸形科普助手</h1>
            <p>可以询问耳畸形相关的一般知识、就诊准备与护理常识。</p>
            <div className="suggestion-list">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => {
                    onSuggestion(suggestion);
                    requestAnimationFrame(() => textareaRef.current?.focus());
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="messages">
            {messages.map((message) => (
              <article className={`message-row ${message.role}`} key={message.id}>
                {message.role === "assistant" && (
                  <div className="message-avatar" aria-hidden="true">
                    <Sparkles size={16} />
                  </div>
                )}
                <div className="message-content">
                  <span className="message-author">
                    {message.role === "assistant" ? "科普助手" : "我"}
                    {message.status === "streaming" && <small>正在生成</small>}
                  </span>
                  {message.status === "streaming" && !message.content ? (
                    <div className="typing" aria-label="正在生成回答">
                      <i />
                      <i />
                      <i />
                    </div>
                  ) : message.role === "assistant" ? (
                    <div
                      className={`message-body ${
                        message.status === "streaming" ? "is-streaming" : ""
                      }`}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                      {message.status === "streaming" && (
                        <span className="stream-cursor" aria-hidden="true" />
                      )}
                    </div>
                  ) : (
                    <p className={message.status === "streaming" ? "is-streaming" : undefined}>
                      {message.content}
                    </p>
                  )}
                  {message.status === "interrupted" && (
                    <small className="message-status">回答未完整生成</small>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <footer className="composer-area">
        {error && (
          <div className="error-banner" role="alert">
            <AlertCircle size={16} />
            {error}
          </div>
        )}
        <form className="composer" onSubmit={submit}>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSubmit();
              }
            }}
            placeholder={sending ? "回答生成中，可随时停止" : "请输入想了解的问题"}
            aria-label="咨询问题"
            rows={1}
            maxLength={4000}
            disabled={sending}
          />
          {sending ? (
            <button
              className="send-button stop-button"
              type="button"
              onClick={onStop}
              title="停止生成"
              aria-label="停止生成"
            >
              <Square size={16} fill="currentColor" />
            </button>
          ) : (
            <button
              className="send-button"
              type="submit"
              disabled={value.trim().length === 0}
              title="发送"
              aria-label="发送问题"
            >
              <Send size={19} />
            </button>
          )}
        </form>
        <p className="medical-notice">内容仅供健康科普，不能替代医生面诊、诊断或治疗建议。</p>
      </footer>
    </section>
  );
}
