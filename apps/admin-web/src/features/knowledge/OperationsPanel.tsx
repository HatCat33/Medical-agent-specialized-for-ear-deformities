import { Button, Drawer, Progress, Select, Table, Tag, Tabs } from "antd";
import { useEffect, useState } from "react";
import { type Operation } from "../../lib/api";
import { downloadJson, labels, useAdminData } from "./shared";
import { ErrorNotice } from "./ErrorNotice";
import { SystemOperations } from "../system/SystemOperations";

export function OperationsPanel({ token, revision }: { token: string; revision: number }) {
  return <Tabs items={[
    { key: "knowledge", label: "知识库任务", children: <KnowledgeOperations token={token} revision={revision} /> },
    { key: "system", label: "系统操作审计", children: <SystemOperations token={token} /> },
  ]} />;
}

function KnowledgeOperations({ token, revision }: { token: string; revision: number }) {
  const [tick, setTick] = useState(0);
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<Operation | null>(null);
  const data = useAdminData<{ items: Operation[]; total: number }>(token,
    `/operations?page=${page}${kind ? `&kind=${kind}` : ""}`, revision + tick);
  const full = useAdminData<Operation>(token, selected ? `/operations/${selected.id}` : null, tick);
  useEffect(() => {
    const timer = setInterval(() => setTick(n => n + 1), 3000);
    return () => clearInterval(timer);
  }, []);
  const detail = full.data?.id === selected?.id ? full.data : selected;
  return <>
    <ErrorNotice error={data.error} />
    <div className="toolbar"><Select aria-label="记录类型" value={kind} onChange={v => { setKind(v); setPage(1); }} options={[
      { value: "", label: "全部操作" }, { value: "embedding", label: "向量构建任务" }, { value: "evaluation", label: "历史评测报告" },
    ]} /><span className="muted">每 3 秒刷新。失败后已完成批次保留，可再次补全；全量重建失败需重新全量执行。</span></div>
    <Table<Operation> rowKey="id" dataSource={data.data?.items} scroll={{ x: 780 }}
      pagination={{ current: page, pageSize: 30, total: data.data?.total, onChange: setPage, showSizeChanger: false }} columns={[
        { title: "操作", dataIndex: "kind", render: (s: string) => labels[s] ?? s },
        { title: "状态", dataIndex: "status", render: (s: string) => <Tag color={s === "completed" ? "green" : s === "failed" ? "red" : "blue"}>{labels[s] ?? s}</Tag> },
        { title: "进度", width: 180, render: (_, op) => op.kind === "embedding" ? <><Progress percent={op.total ? Math.round(op.processed / op.total * 100) : op.status === "completed" ? 100 : 0} size="small" />{op.processed} / {op.total}</> : "—" },
        { title: "时间", dataIndex: "created_at", render: (v: string) => new Date(v).toLocaleString() },
        { title: "详情", render: (_, op) => <Button onClick={() => setSelected(op)}>查看</Button> },
      ]} />
    <Drawer title="操作详情" open={!!selected} onClose={() => setSelected(null)} width={700}>
      <ErrorNotice error={full.error} />
      {detail && <><p>记录 ID：<code>{detail.id}</code></p><p>版本 ID：<code>{detail.release_id}</code></p>
        {detail.error && <ErrorNotice error={detail.error} />}<pre className="content-preview">{JSON.stringify(detail.detail, null, 2)}</pre>
        <Button disabled={full.data?.id !== selected?.id} onClick={() => downloadJson(detail, `knowledge-${detail.id}.json`)}>导出记录</Button></>}
    </Drawer>
  </>;
}
