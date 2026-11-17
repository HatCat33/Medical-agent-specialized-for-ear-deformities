import { Progress, Spin, Table, Tag, Tooltip } from "antd";
import { useEffect, useState } from "react";

import { type CapacityInventory, type CapacityMetric, systemApi } from "../../lib/api";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText } from "../knowledge/shared";

const REFRESH_MS = 3000;

function milliseconds(value: number | null): string {
  if (value === null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(1)} 秒`;
}

function pressure(row: CapacityMetric) {
  if (row.waiting > 0) return <Tag color="orange">正在排队</Tag>;
  if (row.active > 0) return <Tag color="blue">正在处理</Tag>;
  if (row.total_rejected > 0) return <Tag color="red">曾发生拒绝</Tag>;
  return <Tag color="green">空闲</Tag>;
}

export function CapacityPanel({ token, embedded = false }: { token: string; embedded?: boolean }) {
  const [data, setData] = useState<CapacityInventory | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let running = false;
    async function load() {
      if (running || document.hidden) return;
      running = true;
      try {
        const result = await systemApi<CapacityInventory>(token, "/capacity");
        if (active) { setData(result); setError(""); }
      } catch (reason) {
        if (active) setError(errorText(reason));
      } finally {
        running = false;
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    const visible = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [token]);

  return <section className={embedded ? "overview-tab-panel capacity-section" : "knowledge-section capacity-section"}>
    <div className="section-heading capacity-heading">
      <div>
        <h2>{embedded ? "实时容量" : "模型并发与排队"}</h2>
        <span>当前进程实时状态 · 最近 5 分钟等待分布 · 每 3 秒刷新</span>
      </div>
    </div>
    <ErrorNotice error={error} />
    {!data && !error ? <Spin /> : data && <Table<CapacityMetric>
      className="capacity-table"
      rowKey="scope"
      size="small"
      pagination={false}
      scroll={{ x: 880 }}
      dataSource={data.items}
      columns={[
        { title: "能力", dataIndex: "label", width: 105, render: (value, row) => <div className="capacity-name"><strong>{value}</strong>{pressure(row)}</div> },
        { title: "实时状态", width: 135, render: (_, row) => <div className="capacity-live"><strong>{row.active} / {row.limit || "不限"}</strong><span>{row.waiting > 0 ? `${row.waiting} 个等待` : "无人等待"}</span></div> },
        { title: "占用率", width: 135, render: (_, row) => row.utilization_percent === null ? "未限流" : <Progress percent={Math.min(100, row.utilization_percent)} size="small" status={row.utilization_percent >= 100 ? "exception" : "normal"} /> },
        { title: <Tooltip title="最近 5 分钟内成功取得名额和被拒绝请求的等待时间">等待 P50 / P95</Tooltip>, width: 160, render: (_, row) => <span>{milliseconds(row.p50_wait_ms)} / {milliseconds(row.p95_wait_ms)}</span> },
        { title: "最大等待", width: 105, render: (_, row) => milliseconds(row.max_wait_ms) },
        { title: "近期样本", dataIndex: "recent_samples", width: 90 },
        { title: "累计通过 / 拒绝", width: 125, render: (_, row) => <span>{row.total_acquired.toLocaleString("zh-CN")} / <b className={row.total_rejected ? "capacity-rejected" : ""}>{row.total_rejected.toLocaleString("zh-CN")}</b></span> },
        { title: "排队上限", dataIndex: "wait_seconds", width: 90, render: value => `${value} 秒` },
      ]}
    />}
  </section>;
}
