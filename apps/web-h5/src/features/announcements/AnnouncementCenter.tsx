import { Bell, CheckCircle2, ChevronRight, Clock3, Eye, Info, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Announcement } from "../../lib/types";

interface AnnouncementCenterProps {
  open: boolean;
  announcements: Announcement[];
  selected: Announcement | null;
  readIds: Set<string>;
  onClose: () => void;
  onSelect: (announcement: Announcement) => void;
}

function displayDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function relativeDate(value: string): string {
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86_400_000));
  if (days === 0) return "今天";
  if (days === 1) return "1天前";
  if (days < 30) return `${days}天前`;
  return displayDate(value).slice(0, 10);
}

function useFloatingScrollbar<ElementType extends HTMLElement>(dependencyKey: string | number) {
  const scrollRef = useRef<ElementType>(null);
  const [indicator, setIndicator] = useState({ visible: false, height: 0, top: 0 });

  const update = useCallback(() => {
    const element = scrollRef.current;
    if (!element || element.scrollHeight <= element.clientHeight) {
      setIndicator({ visible: false, height: 0, top: 0 });
      return;
    }
    const height = Math.max(52, (element.clientHeight / element.scrollHeight) * element.clientHeight);
    const top = (element.scrollTop / (element.scrollHeight - element.clientHeight)) * (element.clientHeight - height);
    setIndicator({ visible: true, height, top });
  }, []);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    for (const child of element.children) observer.observe(child);
    return () => observer.disconnect();
  }, [dependencyKey, update]);

  return { scrollRef, indicator, update };
}

export function AnnouncementCenter({
  open, announcements, selected, readIds, onClose, onSelect,
}: AnnouncementCenterProps) {
  const contentScroll = useFloatingScrollbar<HTMLElement>(`${open}:${selected?.id ?? ""}:${selected?.content.length ?? 0}`);
  const listScroll = useFloatingScrollbar<HTMLDivElement>(`${open}:${announcements.length}:${selected?.id ?? "list"}`);
  if (!open) return null;
  return <div className="announcement-overlay" role="presentation" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="announcement-dialog" role="dialog" aria-modal="true" aria-label={selected ? selected.title : "公告"}>
      {selected ? <>
        <header className="announcement-detail-header">
          <div className="announcement-detail-topline">
            <div className="announcement-detail-icon"><Info size={20} /></div>
            <span className="announcement-badge">公告</span>
          </div>
          <button className="announcement-close" type="button" onClick={onClose} aria-label="关闭公告"><X size={22} /></button>
          <h2>{selected.title}</h2>
          <div className="announcement-meta">
            <span><Clock3 size={14} />{relativeDate(selected.published_at)} · {displayDate(selected.published_at)}</span>
            <span><Eye size={15} />已读</span>
          </div>
        </header>
        <div className="announcement-content-shell">
          <article className="announcement-content" ref={contentScroll.scrollRef} onScroll={contentScroll.update}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer">{children}</a>,
              }}
            >
              {selected.content}
            </ReactMarkdown>
          </article>
          {contentScroll.indicator.visible ? <span className="announcement-scroll-indicator" aria-hidden="true" style={{ height: contentScroll.indicator.height, transform: `translateY(${contentScroll.indicator.top}px)` }} /> : null}
        </div>
        <footer className="announcement-detail-footer">
          <span className="announcement-read-state"><Info size={15} />您已阅读此公告</span>
          <button className="announcement-finish" type="button" onClick={onClose}>关闭</button>
        </footer>
      </> : <>
        <header className="announcement-list-header">
          <div className="announcement-detail-icon"><Bell size={20} /></div>
          <h2>公告</h2>
          <button className="announcement-close" type="button" onClick={onClose} aria-label="关闭公告"><X size={22} /></button>
        </header>
        <div className="announcement-list-shell">
          <div className="announcement-list" ref={listScroll.scrollRef} onScroll={listScroll.update}>
            {announcements.length ? announcements.map(item => <button key={item.id} type="button" className="announcement-list-item" onClick={() => onSelect(item)}>
              <span className={`announcement-item-mark ${readIds.has(item.id) ? "is-read" : ""}`}><CheckCircle2 size={17} /></span>
              <span className="announcement-item-copy"><strong>{item.title}</strong><small>{relativeDate(item.published_at)}</small></span>
              {!readIds.has(item.id) ? <span className="announcement-new">新</span> : null}
              <ChevronRight size={19} />
            </button>) : <div className="announcement-empty"><Bell size={24} /><p>暂无公告</p></div>}
          </div>
          {listScroll.indicator.visible ? <span className="announcement-scroll-indicator" aria-hidden="true" style={{ height: listScroll.indicator.height, transform: `translateY(${listScroll.indicator.top}px)` }} /> : null}
        </div>
      </>}
    </section>
  </div>;
}
