import { Alert, Button, Input, InputNumber, Modal, Progress, Table, Tag, Upload } from "antd";
import { useState } from "react";
import { knowledgeApi, type Operation, type Release } from "../../lib/api";
import { errorText, labels } from "./shared";
import { ErrorNotice } from "./ErrorNotice";

interface Props { token: string; release?: Release; releases: Release[]; embeddingConfigured: boolean; onChanged: (id?: string) => void }
export function VersionsPanel({ token, release, releases, embeddingConfigured, onChanged }: Props) {
  const [name, setName] = useState("");
  const [batch, setBatch] = useState(32);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState<"publish" | "delete" | "rebuild" | null>(null);
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await fn(); } catch (e) { setError(errorText(e)); }
    finally { setBusy(false); setConfirm(null); }
  }
  async function create(base: boolean) {
    await action(async () => {
      const result = await knowledgeApi<Release>(token, "/releases", { method: "POST", body: JSON.stringify({
        name: name.trim() || `${release?.name ?? "医院 FAQ"} · 新草稿`, base_release_id: base ? release?.id : null,
      }) }); onChanged(result.id); setName(""); setNotice("草稿已创建，可进入分块管理编辑内容。");
    });
  }
  async function job(full: boolean) {
    if (!release) return;
    await action(async () => {
      const result = await knowledgeApi<Operation>(token, `/releases/${release.id}/jobs`, {
        method: "POST", body: JSON.stringify({ batch_size: batch, rebuild_all: full }),
      }); setNotice(`任务已提交：${result.id}。在“任务与记录”查看进度，关闭页面不会取消任务。`); onChanged();
    });
  }
  async function confirmed() {
    if (!release) return;
    if (confirm === "rebuild") return job(true);
    await action(async () => {
      await knowledgeApi(token, `/releases/${release.id}${confirm === "publish" ? "/publish" : ""}`,
        { method: confirm === "publish" ? "POST" : "DELETE" });
      onChanged(); setNotice(confirm === "publish" ? "版本已发布，后续患者问答使用此版本。" : "草稿已删除。");
    });
  }
  return <>
    <ErrorNotice error={error} />{notice && <Alert type="success" message={notice} showIcon />}
    <section className="knowledge-section">
      <h2>创建知识版本</h2><div className="toolbar">
        <Input aria-label="新版本名称" placeholder="新版本名称" maxLength={120} value={name} onChange={e => setName(e.target.value)} />
        <Button type="primary" disabled={!release} loading={busy} onClick={() => void create(true)}>复制当前版本为草稿</Button>
        <Button disabled={busy} onClick={() => void create(false)}>新建空白草稿</Button>
      </div>
    </section>
    {release && <section className="knowledge-section">
      <div className="section-heading"><h2>{release.name}</h2><Tag>{labels[release.status]}</Tag></div>
      <p>{release.entry_count} 个 chunk · {release.vectorized_count} 个已向量化 · {release.pending_count} 个待处理</p>
      <Progress percent={release.entry_count ? Math.round(release.vectorized_count / release.entry_count * 100) : 0} />
      <div className="toolbar">
        <label>批次大小 <InputNumber aria-label="批次大小" min={1} max={256} value={batch} onChange={v => setBatch(v ?? 32)} /></label>
        <Button type="primary" loading={busy} disabled={!embeddingConfigured || release.pending_count === 0} onClick={() => void job(false)}>补全缺失向量</Button>
        <Button disabled={busy || !embeddingConfigured || release.status !== "draft"} onClick={() => setConfirm("rebuild")}>全量重建</Button>
      </div>
      <div className="toolbar">
        <Upload accept=".xlsx" showUploadList={false} disabled={busy || release.status !== "draft"}
          beforeUpload={file => { void action(async () => {
            const form = new FormData(); form.append("file", file);
            const result = await knowledgeApi<{ added: number; updated: number; unchanged: number; warnings: string[] }>(token, `/releases/${release.id}/import`, { method: "POST", body: form });
            setNotice(`导入完成：新增 ${result.added}，更新 ${result.updated}，未变更 ${result.unchanged}。${result.warnings.join(" ")}`); onChanged();
          }); return false; }}><Button disabled={busy || release.status !== "draft"}>增量导入 XLSX</Button></Upload>
        <Button disabled={busy || !release.entry_count || release.status === "published"} onClick={() => setConfirm("publish")}>{release.status === "archived" ? "回退到此版本" : "发布版本"}</Button>
        <Button danger disabled={busy || release.status !== "draft"} onClick={() => setConfirm("delete")}>删除草稿</Button>
      </div>
      <p className="muted">XLSX 必需列：分类、标准问题、答案内容；可选：其他问法、有效日期。按标准问题匹配更新，未出现的旧条目保留。要修改问题本身，请到分块管理编辑。</p>
    </section>}
    <Table<Release> rowKey="id" dataSource={releases} pagination={{ pageSize: 8 }} scroll={{ x: 640 }} columns={[
      { title: "版本", dataIndex: "name", render: (name: string, row) => <Button type="link" onClick={() => onChanged(row.id)}>{name}</Button> },
      { title: "状态", dataIndex: "status", render: (s: string) => labels[s] },
      { title: "条目", dataIndex: "entry_count" }, { title: "待向量化", dataIndex: "pending_count" },
      { title: "来源", dataIndex: "source_name" },
    ]} />
    <Modal open={!!confirm} title={confirm === "delete" ? "删除草稿" : confirm === "rebuild" ? "确认全量重建" : "确认发布版本"}
      onCancel={() => setConfirm(null)} onOk={() => void confirmed()} confirmLoading={busy} okText="确认" cancelText="取消">
      <p>{confirm === "delete" ? "将删除此草稿及全部 chunk，已发布和历史版本仍保留。" : confirm === "rebuild" ? "重新计算当前草稿的所有向量，会调用已配置的 Embedding 服务。" : `发布后患者端立即使用此版本；当前有 ${release?.pending_count ?? 0} 条缺少向量，将使用关键词检索。`}</p>
    </Modal>
  </>;
}
