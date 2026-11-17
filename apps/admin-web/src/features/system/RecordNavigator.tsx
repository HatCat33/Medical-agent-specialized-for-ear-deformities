import { Empty, Input, Pagination, Select, Tag } from "antd";
import { useState, type ReactNode } from "react";
import { labels } from "../knowledge/shared";

interface RecordItem { id: string; title: string; status: string; date: string | null }

/** Keep long version histories searchable without crowding the editor. */
export function RecordNavigator({ items, selectedId, disabled, onSelect, title, action, feedback }: {
  items: RecordItem[]; selectedId: string; disabled: boolean; onSelect: (id: string) => void;
  title: string; action: ReactNode; feedback?: ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const filtered = items.filter(item => (status === "all" || item.status === status)
    && item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const currentPage = Math.min(page, Math.max(1, Math.ceil(filtered.length / 8)));
  return <aside className="record-navigator">
    <div className="record-nav-heading"><h2>{title}</h2>{action}</div>
    {feedback}
    <Input aria-label={`搜索${title}`} value={query} allowClear placeholder="搜索名称" disabled={disabled}
      onChange={event => { setQuery(event.target.value); setPage(1); }} />
    <Select aria-label={`${title}状态`} value={status} disabled={disabled} onChange={value => { setStatus(value); setPage(1); }} options={[
      { value: "all", label: `全部记录 · ${items.length}` }, { value: "draft", label: "草稿" },
      { value: "published", label: "已发布" },
      ...(items.some(item => item.status === "archived") ? [{ value: "archived", label: "历史版本" }] : []),
    ]} />
    <div className="record-nav-list">
      {filtered.slice((currentPage - 1) * 8, currentPage * 8).map(item => <button key={item.id} type="button"
        className={`record-nav-item${item.id === selectedId ? " is-selected" : ""}`} disabled={disabled}
        aria-pressed={item.id === selectedId} onClick={() => onSelect(item.id)}>
        <strong>{item.title}</strong><span><Tag color={item.status === "published" ? "green" : item.status === "draft" ? "blue" : undefined}>{labels[item.status] ?? item.status}</Tag>
          <time>{item.date ? new Date(item.date).toLocaleDateString("zh-CN") : "尚未发布"}</time></span>
      </button>)}
      {!filtered.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={items.length ? "没有符合条件的记录" : "暂无记录"} />}
    </div>
    {filtered.length > 8 && <Pagination size="small" simple current={currentPage} pageSize={8} total={filtered.length} onChange={setPage} showSizeChanger={false} />}
  </aside>;
}
