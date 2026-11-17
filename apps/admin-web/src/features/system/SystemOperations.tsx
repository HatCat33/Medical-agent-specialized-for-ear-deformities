import { Button, Drawer, Table, Tag } from "antd";
import { useEffect, useState } from "react";

import { type ModelProbe, systemApi } from "../../lib/api";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText } from "../knowledge/shared";

const names: Record<string, string> = {
  prompt_created: "创建提示词", prompt_updated: "修改提示词", prompt_deleted: "删除提示词",
  prompt_published: "发布 / 回退提示词", model_probe: "模型探活", speech_settings_updated: "修改声音设置", speech_preview_failed: "云端试听失败",
};

export function SystemOperations({ token }: { token: string }) {
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<{ items: ModelProbe[]; total: number }>({ items: [], total: 0 });
  const [selected, setSelected] = useState<ModelProbe | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void systemApi<typeof data>(token, `/operations?page=${page}`)
      .then(value => { if (active) { setData(value); setError(""); } })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); });
    return () => { active = false; };
  }, [token, page, revision]);
  return <>
    <ErrorNotice error={error} />
    <div className="toolbar"><Button onClick={() => setRevision(value => value + 1)}>刷新审计</Button></div>
    <Table<ModelProbe> rowKey="id" dataSource={data.items} scroll={{ x: 620 }} pagination={{ current: page, pageSize: 30, total: data.total, onChange: setPage, showSizeChanger: false }} columns={[
      { title: "操作", dataIndex: "kind", render: (value: string) => names[value] ?? value },
      { title: "目标", dataIndex: "target_id", ellipsis: true },
      { title: "结果", dataIndex: "status", render: (value: string) => <Tag color={["healthy", "completed"].includes(value) ? "green" : value === "unhealthy" ? "red" : "default"}>{({ healthy: "正常", completed: "完成", unhealthy: "异常", not_configured: "未配置", client_only: "设备能力" } as Record<string, string>)[value] ?? value}</Tag> },
      { title: "时间", dataIndex: "created_at", render: (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false }) },
      { title: "详情", render: (_, row) => <Button onClick={() => setSelected(row)}>查看</Button> },
    ]} />
    <Drawer title="系统操作详情" open={!!selected} onClose={() => setSelected(null)} width={620}><pre className="content-preview">{JSON.stringify(selected, null, 2)}</pre></Drawer>
  </>;
}
