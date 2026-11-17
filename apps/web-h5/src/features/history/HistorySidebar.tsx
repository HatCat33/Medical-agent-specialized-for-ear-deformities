import { useCallback, useEffect, useRef, useState } from "react";

import {
  Bell,
  Check,
  Copy,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import type { Conversation } from "../../lib/types";

interface HistorySidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  collapsed: boolean;
  busy: boolean;
  userReference: string;
  unreadAnnouncements: number;
  onClose: () => void;
  onToggleCollapsed: () => void;
  onCreate: () => void;
  onSelect: (conversationId: string) => void;
  onDelete: (conversationId: string) => void;
  onOpenAnnouncements: () => void;
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
  collapsed,
  busy,
  userReference,
  unreadAnnouncements,
  onClose,
  onToggleCollapsed,
  onCreate,
  onSelect,
  onDelete,
  onOpenAnnouncements,
}: HistorySidebarProps) {
  const historyListRef = useRef<HTMLDivElement>(null);
  const [scrollIndicator, setScrollIndicator] = useState({ visible: false, height: 0, top: 0 });
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<number | null>(null);

  const updateScrollIndicator = useCallback(() => {
    const list = historyListRef.current;
    if (!list || list.scrollHeight <= list.clientHeight) {
      setScrollIndicator({ visible: false, height: 0, top: 0 });
      return;
    }
    const height = Math.max(56, (list.clientHeight / list.scrollHeight) * list.clientHeight);
    const top = (list.scrollTop / (list.scrollHeight - list.clientHeight)) * (list.clientHeight - height);
    setScrollIndicator({ visible: true, height, top });
  }, []);

  useEffect(() => {
    const list = historyListRef.current;
    if (!list) return;
    updateScrollIndicator();
    const observer = new ResizeObserver(updateScrollIndicator);
    observer.observe(list);
    for (const child of list.children) observer.observe(child);
    return () => observer.disconnect();
  }, [conversations, updateScrollIndicator]);

  async function copyUserReference() {
    if (!userReference || userReference === "暂不可用") return;
    await navigator.clipboard.writeText(userReference);
    setCopied(true);
    if (copiedTimer.current !== null) window.clearTimeout(copiedTimer.current);
    copiedTimer.current = window.setTimeout(() => setCopied(false), 1600);
  }

  useEffect(() => () => {
    if (copiedTimer.current !== null) window.clearTimeout(copiedTimer.current);
  }, []);

  const orderedConversations = [...conversations].sort((left, right) => {
    const leftTime = new Date(left.last_message_at ?? left.created_at).getTime();
    const rightTime = new Date(right.last_message_at ?? right.created_at).getTime();
    return rightTime - leftTime;
  });

  return (
    <aside
      className={`history-sidebar ${open ? "is-open" : ""} ${collapsed ? "is-collapsed" : ""}`}
      aria-label="历史会话"
    >
      <div className="history-header">
        <div className="history-title">
          <span className="history-title-mark" aria-hidden="true"><MessageSquareText size={17} /></span>
          <span className="history-title-copy"><strong>历史会话</strong><small>{conversations.length} 条会话</small></span>
        </div>
        <button
          className="icon-button sidebar-collapse-button desktop-only"
          type="button"
          onClick={onToggleCollapsed}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
        >
          {collapsed ? <PanelLeftOpen size={20} /> : <PanelLeftClose size={20} />}
        </button>
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
        <Plus size={18} />
        <span>新建会话</span>
      </button>

      <div className="history-body">
        <div className="history-list" ref={historyListRef} onScroll={updateScrollIndicator}>
          {conversations.length === 0 ? (
            <div className="history-empty"><MessageSquareText size={21} /><p>还没有历史会话</p><span>发出第一条问题后会保存在这里</span></div>
          ) : (
            orderedConversations.map((conversation) => (
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
                  <span className="history-item-copy">
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
        {scrollIndicator.visible ? (
          <span
            className="history-scroll-indicator"
            aria-hidden="true"
            style={{ height: scrollIndicator.height, transform: `translateY(${scrollIndicator.top}px)` }}
          />
        ) : null}
      </div>

      <div className="history-user-reference" title="如需协助定位记录，请提供此用户 ID">
        <div className="history-user-reference-main">
          <div className="history-user-reference-label">
            <span>用户 ID</span>
            <button
              className={copied ? "is-copied" : ""}
              type="button"
              onClick={() => void copyUserReference()}
              disabled={!userReference || userReference === "暂不可用"}
              title={copied ? "已复制" : "复制用户 ID"}
              aria-label={copied ? "用户 ID 已复制" : "复制用户 ID"}
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
            </button>
          </div>
          <code>{userReference || "正在生成…"}</code>
        </div>
        <button
          className="history-announcement-button"
          type="button"
          onClick={onOpenAnnouncements}
          title="查看公告"
          aria-label={unreadAnnouncements ? `查看公告，${unreadAnnouncements} 条未读` : "查看公告"}
        >
          <Bell size={18} />
          {unreadAnnouncements > 0 ? <span className="announcement-unread-dot" /> : null}
        </button>
      </div>
    </aside>
  );
}
