import { Button, Drawer, Empty, Input, Modal, Pagination, Spin, Table, Tag } from "antd";
import { Download, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { exportInteractionRecords, type InteractionAnalytics, knowledgeApi } from "../../lib/api";
import { ErrorNotice } from "./ErrorNotice";
import { errorText } from "./shared";

interface Filters {
  startDate: string;
  endDate: string;
  userId: string;
}

type InteractionItem = InteractionAnalytics["items"][number];

function queryString(filters: Filters, page: number): string {
  const params = new URLSearchParams({ page: String(page), page_size: "20" });
  if (filters.startDate) params.set("start_date", filters.startDate);
  if (filters.endDate) params.set("end_date", filters.endDate);
  if (filters.userId.trim()) params.set("user_id", filters.userId.trim());
  return params.toString();
}

function filterQueryString(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.startDate) params.set("start_date", filters.startDate);
  if (filters.endDate) params.set("end_date", filters.endDate);
  if (filters.userId.trim()) params.set("user_id", filters.userId.trim());
  return params.toString();
}

function TrendChart({ data, loading }: { data: InteractionAnalytics["trend"]; loading: boolean }) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const width = 760;
  const height = 246;
  const padding = { top: 18, right: 20, bottom: 38, left: 44 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const maximum = Math.max(1, ...data.map(item => item.interactions));
  const maxValue = Math.max(2, Math.ceil(maximum * 1.14));
  const step = chartWidth / Math.max(1, data.length);
  const barWidth = Math.min(18, Math.max(4, step * 0.3));
  const points = data.map((item, index) => ({
    ...item,
    x: padding.left + step * (index + 0.5),
    y: padding.top + chartHeight - item.interactions / maxValue * chartHeight,
  }));
  const path = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const tickStep = Math.max(1, Math.ceil(data.length / 6));
  const tickIndexes = data
    .map((_, index) => index)
    .filter(index => index % tickStep === 0 || index === data.length - 1);
  const activePoint = hoveredIndex === null ? null : points[hoveredIndex];
  const tooltipWidth = 156;
  const tooltipHeight = 82;
  const tooltipX = activePoint
    ? activePoint.x + tooltipWidth + 18 <= width - padding.right
      ? activePoint.x + 13
      : activePoint.x - tooltipWidth - 13
    : 0;
  const tooltipY = activePoint
    ? Math.min(padding.top + chartHeight - tooltipHeight, Math.max(padding.top, activePoint.y - tooltipHeight / 2))
    : 0;
  const animationKey = data.map(item => `${item.key}:${item.interactions}`).join("|");

  if (!data.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前条件下没有趋势数据" />;
  return <div className="analytics-chart-scroll">
    <svg className={`analytics-trend-chart${loading ? " is-loading" : ""}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="交互次数柱线组合趋势图" onPointerLeave={() => setHoveredIndex(null)}>
      {[0, 0.25, 0.5, 0.75, 1].map(ratio => {
        const y = padding.top + chartHeight * ratio;
        const value = Math.round(maxValue * (1 - ratio));
        return <g className="chart-grid" key={ratio}><line x1={padding.left} y1={y} x2={width - padding.right} y2={y} /><text x={padding.left - 9} y={y + 4} textAnchor="end">{value}</text></g>;
      })}
      {activePoint && <g className="trend-hover-guide">
        <rect x={activePoint.x - step / 2} y={padding.top} width={step} height={chartHeight} />
        <line x1={activePoint.x} y1={padding.top} x2={activePoint.x} y2={padding.top + chartHeight} />
      </g>}
      <g className="trend-series" key={animationKey}>
        {points.map((point, index) => <rect
          className={`trend-bar${hoveredIndex === index ? " is-active" : ""}`}
          key={`bar-${point.key}`}
          x={point.x - barWidth / 2}
          y={point.y}
          width={barWidth}
          height={padding.top + chartHeight - point.y}
          rx={Math.min(5, barWidth / 3)}
          style={{ animationDelay: `${Math.min(index * 28, 320)}ms` }}
        />)}
        <path className="trend-line" d={path} pathLength="1" />
        {points.map((point, index) => <circle
          className={hoveredIndex === index ? "is-active" : ""}
          key={`point-${point.key}`}
          cx={point.x}
          cy={point.y}
          r={hoveredIndex === index ? 5 : 3.5}
        />)}
      </g>
      {tickIndexes.map(index => <text className="axis-label" key={data[index].key} x={points[index].x} y={height - 10} textAnchor="middle">{data[index].label}</text>)}
      {points.map((point, index) => <rect
        className="trend-hit-area"
        key={`hit-${point.key}`}
        x={point.x - step / 2}
        y={padding.top}
        width={step}
        height={chartHeight}
        onPointerEnter={() => setHoveredIndex(index)}
        onPointerMove={() => setHoveredIndex(index)}
      />)}
      {activePoint && <g transform={`translate(${tooltipX} ${tooltipY})`} pointerEvents="none">
        <g className="trend-tooltip">
          <rect width={tooltipWidth} height={tooltipHeight} rx="8" />
          <text className="tooltip-date" x="13" y="20">{activePoint.label}</text>
          <circle cx="16" cy="39" r="3" />
          <text x="27" y="43">交互次数</text>
          <text className="tooltip-value" x={tooltipWidth - 13} y="43" textAnchor="end">{activePoint.interactions}</text>
          <text x="13" y="65">参与用户 {activePoint.users}</text>
          <text x={tooltipWidth - 13} y="65" textAnchor="end">人均 {(activePoint.interactions / Math.max(1, activePoint.users)).toFixed(2)}</text>
        </g>
      </g>}
    </svg>
  </div>;
}

function UserBars({ data }: { data: InteractionAnalytics["top_users"] }) {
  const maximum = Math.max(1, ...data.map(item => item.interactions));
  if (!data.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前条件下没有用户数据" />;
  return <div className="analytics-user-bars">
    {data.map(item => <div key={item.user_id} className="analytics-user-row">
      <code>{item.user_id}</code>
      <span><i style={{ width: `${Math.max(4, item.interactions / maximum * 100)}%` }} /></span>
      <b>{item.interactions}</b>
    </div>)}
  </div>;
}

export function InteractionAnalyticsPanel({ token }: { token: string }) {
  const [draft, setDraft] = useState<Filters>({ startDate: "", endDate: "", userId: "" });
  const [filters, setFilters] = useState<Filters>(draft);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<InteractionAnalytics | null>(null);
  const [selected, setSelected] = useState<InteractionItem | null>(null);
  const [exportConfirmOpen, setExportConfirmOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const [liveRevision, setLiveRevision] = useState(0);
  const requestPath = useMemo(() => `/interaction-analytics?${queryString(filters, page)}`, [filters, page]);

  useEffect(() => {
    if (filters.endDate) return;
    const timer = window.setInterval(() => setLiveRevision(current => current + 1), 30_000);
    return () => window.clearInterval(timer);
  }, [filters.endDate]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void knowledgeApi<InteractionAnalytics>(token, requestPath)
      .then(result => { if (active) { setData(result); setError(""); } })
      .catch(reason => { if (active) setError(errorText(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [liveRevision, requestPath, token]);

  function search() {
    setPage(1);
    setFilters({ ...draft, userId: draft.userId.trim() });
  }

  function clear() {
    const empty = { startDate: "", endDate: "", userId: "" };
    setDraft(empty);
    setFilters(empty);
    setPage(1);
  }

  async function exportRecords() {
    setExportConfirmOpen(false);
    setExporting(true);
    try {
      const result = await exportInteractionRecords(token, filterQueryString(filters));
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setError("");
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setExporting(false);
    }
  }

  return <>
    <div className="analytics-filter-bar">
      <label><span>开始日期</span><input type="date" value={draft.startDate} max={draft.endDate || undefined} onChange={event => setDraft(current => ({ ...current, startDate: event.target.value }))} /></label>
      <label><span>结束日期</span><input type="date" value={draft.endDate} min={draft.startDate || undefined} onChange={event => setDraft(current => ({ ...current, endDate: event.target.value }))} /></label>
      <label className="analytics-user-filter"><span>用户 ID</span><Input placeholder="例如 AB3D-6C56-8D61-EEBF" value={draft.userId} onChange={event => setDraft(current => ({ ...current, userId: event.target.value }))} onPressEnter={search} /></label>
      <Button type="primary" icon={<Search size={15} />} onClick={search}>查询</Button>
      <Button icon={<X size={15} />} onClick={clear}>清空</Button>
      <Button icon={<Download size={15} />} loading={exporting} disabled={!data?.summary.matched_interactions} onClick={() => setExportConfirmOpen(true)}>导出交互记录</Button>
    </div>
    <ErrorNotice error={error} />
    {loading && !data ? <Spin /> : data && <>
      <div className="analytics-summary">
        <div title="至少完成过一次提问、当前数据库仍保留交互记录的去重用户数"><span>总用户数</span><strong>{data.summary.total_users.toLocaleString("zh-CN")}</strong></div>
        <div><span>范围内用户</span><strong>{data.summary.matched_users.toLocaleString("zh-CN")}</strong></div>
        <div><span>范围内交互</span><strong>{data.summary.matched_interactions.toLocaleString("zh-CN")}</strong></div>
        <div><span>人均交互</span><strong>{data.summary.average_interactions.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</strong></div>
      </div>

      <div className="analytics-visual-grid">
        <section><div className="analytics-subheading analytics-subheading-row"><div><h3>交互趋势</h3><span>查看交互次数随时间的变化</span></div>{!filters.endDate && <span className="analytics-live"><i />自动更新</span>}</div><TrendChart data={data.trend} loading={loading} /></section>
        <section><div className="analytics-subheading"><h3>活跃用户</h3><span>当前筛选范围内交互次数较多的用户</span></div><UserBars data={data.top_users} /></section>
      </div>

      <section className="analytics-records">
        <div className="analytics-subheading"><h3>交互明细</h3><span>共 {data.total.toLocaleString("zh-CN")} 条，可点击查看完整问答</span></div>
        <Table<InteractionItem>
          rowKey="id"
          size="small"
          pagination={false}
          loading={loading}
          scroll={{ x: 880 }}
          dataSource={data.items}
          onRow={record => ({ onClick: () => setSelected(record) })}
          columns={[
            { title: "时间", dataIndex: "created_at", width: 165, render: value => new Date(value).toLocaleString("zh-CN", { hour12: false }) },
            { title: "用户 ID", dataIndex: "user_id", width: 175, render: value => <code>{value}</code> },
            { title: "患者输入", dataIndex: "input", ellipsis: true },
            { title: "模型回复", dataIndex: "output", ellipsis: true, render: value => value || <span className="muted">尚未生成</span> },
            { title: "状态", dataIndex: "status", width: 90, render: value => <Tag color={value === "completed" ? "green" : value === "pending" ? "orange" : "default"}>{value === "completed" ? "已完成" : value === "pending" ? "生成中" : value}</Tag> },
          ]}
        />
        {data.total > data.page_size && <Pagination current={data.page} pageSize={data.page_size} total={data.total} showSizeChanger={false} onChange={setPage} />}
      </section>
    </>}

    <Drawer title="交互详情" open={!!selected} onClose={() => setSelected(null)} width={620}>
      {selected && <div className="analytics-detail">
        <dl>
          <div><dt>时间</dt><dd>{new Date(selected.created_at).toLocaleString("zh-CN", { hour12: false })}</dd></div>
          <div><dt>用户 ID</dt><dd><code>{selected.user_id}</code></dd></div>
          <div><dt>会话</dt><dd>{selected.conversation_title}</dd></div>
          <div><dt>会话 ID</dt><dd><code>{selected.conversation_id}</code></dd></div>
        </dl>
        <section><h3>患者输入</h3><p>{selected.input}</p></section>
        <section><h3>模型回复</h3><p>{selected.output || "尚未生成回复。"}</p></section>
      </div>}
    </Drawer>

    <Modal
      title="确认导出交互记录"
      open={exportConfirmOpen}
      okText="确认导出"
      cancelText="取消"
      confirmLoading={exporting}
      onOk={() => void exportRecords()}
      onCancel={() => setExportConfirmOpen(false)}
    >
      <div className="analytics-export-summary">
        <p>将导出当前筛选范围内已经生成完整回复的交互记录。</p>
        <dl>
          <div><dt>开始时间</dt><dd>{filters.startDate || "最早记录"}</dd></div>
          <div><dt>结束时间</dt><dd>{filters.endDate || "当前时间（导出时）"}</dd></div>
          <div><dt>用户 ID</dt><dd><code>{filters.userId || "全部用户"}</code></dd></div>
          <div><dt>匹配记录</dt><dd>{data?.summary.matched_interactions.toLocaleString("zh-CN") ?? 0} 条</dd></div>
        </dl>
        <small>表格分类固定为“用户交互”，标准问题为用户输入，答案内容为模型回复。</small>
      </div>
    </Modal>
  </>;
}
