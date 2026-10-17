import { Clock3, MessageSquareText, Plus, Trash2, X } from "lucide-react";

import type { Conversation } from "../../lib/types";

interface HistorySidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onCreate: () => void;
  onSelect: (conversationId: string) => void;
  onDelete: (conversationId: string) => void;
}

function displayTime(value: string | null): string {
  if (!value) return "尚未提问";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function HistorySidebar({
  conversations,
  activeId,
  open,
  busy,
  onClose,
  onCreate,
  onSelect,
  onDelete,
}: HistorySidebarProps) {
  return (
    <aside className={`history-sidebar ${open ? "is-open" : ""}`} aria-label="历史会话">
      <div className="history-header">
        <div className="history-title">
          <Clock3 size={18} />
          <span>历史会话</span>
        </div>
        <button
          className="icon-button mobile-only"
          type="button"
          onClick={onClose}
          title="关闭历史会话"
        >
          <X size={19} />
        </button>
      </div>

      <button className="new-chat-button" type="button" onClick={onCreate} disabled={busy}>
        <Plus size={17} />
        新建会话
      </button>

      <div className="history-list">
        {conversations.length === 0 ? (
          <p className="history-empty">暂无历史会话</p>
        ) : (
          conversations.map((conversation) => (
            <div
              className={`history-item ${conversation.id === activeId ? "is-active" : ""}`}
              key={conversation.id}
            >
              <button
                className="history-select"
                type="button"
                disabled={busy}
                onClick={() => onSelect(conversation.id)}
                title={conversation.title}
              >
                <MessageSquareText size={16} />
                <span>
                  <strong>{conversation.title}</strong>
                  <small>{displayTime(conversation.last_message_at)}</small>
                </span>
              </button>
              <button
                className="history-delete"
                type="button"
                disabled={busy}
                onClick={() => onDelete(conversation.id)}
                title="删除会话"
                aria-label={`删除会话：${conversation.title}`}
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
