import { Alert, Button, Drawer, Input, InputNumber, Modal, Progress, Select, Table, Tag, Upload } from "antd";
import { useEffect, useMemo, useState } from "react";
import { knowledgeApi, type ImportPreview, type ImportPreviewRow, type Operation, type Release } from "../../lib/api";
import { errorText, labels } from "./shared";
import { ErrorNotice } from "./ErrorNotice";

interface Props { token: string; release?: Release; releases: Release[]; selectedReleaseId: string; onSelectRelease: (id: string) => void; embeddingConfigured: boolean; draftLimit: number; draftCount: number; canManage: boolean; onChanged: (id?: string) => void }
const kindLabels = { added: "新增", updated: "更新", unchanged: "未变化", conflict: "文件内重复" } as const;
const kindColors = { added: "green", updated: "blue", unchanged: "default", conflict: "orange" } as const;
const fieldLabels: Record<string, string> = { category: "分类", question: "标准问题", aliases: "其他问法", answer: "答案内容", effective_date: "有效日期" };

export function VersionsPanel({ token, release, releases, selectedReleaseId, onSelectRelease, embeddingConfigured, draftLimit, draftCount, canManage, onChanged }: Props) {
  const [name, setName] = useState("");
  const [batch, setBatch] = useState(32);
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createNotice, setCreateNotice] = useState("");
  const [vectorError, setVectorError] = useState("");
  const [vectorNotice, setVectorNotice] = useState("");
  const [versionError, setVersionError] = useState("");
  const [versionNotice, setVersionNotice] = useState("");
  const [importError, setImportError] = useState("");
  const [confirm, setConfirm] = useState<"publish" | "delete" | "rebuild" | "complete" | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importFilter, setImportFilter] = useState("all");
  const [selectedRows, setSelectedRows] = useState<number[]>([]);
  const [comparing, setComparing] = useState<ImportPreviewRow | null>(null);
  useEffect(() => { if (!canManage) { setConfirm(null); setPreview(null); } }, [canManage]);

  async function create(base: boolean) {
    setBusy(true); setCreateError(""); setCreateNotice("");
    try {
      const result = await knowledgeApi<Release>(token, "/releases", { method: "POST", body: JSON.stringify({
        name: name.trim() || `${release?.name ?? "医院知识"} · 新草稿`, base_release_id: base ? release?.id : null,
      }) });
      onChanged(result.id); setName(""); setCreateNotice("草稿已创建，可在下方知识内容区域继续编辑。");
    } catch (reason) { setCreateError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function job(full: boolean) {
    if (!release) return;
    setBusy(true); setVectorError(""); setVectorNotice("");
    try {
      await knowledgeApi<Operation>(token, `/releases/${release.id}/jobs`, {
        method: "POST", body: JSON.stringify({ batch_size: batch, rebuild_all: full }),
      });
      setVectorNotice("向量任务已提交，可刷新查看完成进度。"); setConfirm(null); onChanged();
    } catch (reason) { setVectorError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function confirmed() {
    if (!release) return;
    if (confirm === "rebuild" || confirm === "complete") return job(confirm === "rebuild");
    setBusy(true); setVersionError(""); setVersionNotice("");
    try {
      await knowledgeApi(token, `/releases/${release.id}${confirm === "publish" ? "/publish" : ""}`,
        { method: confirm === "publish" ? "POST" : "DELETE" });
      onChanged(); setVersionNotice(confirm === "publish" ? "版本已发布，后续患者问答将使用此版本。" : "草稿已删除。"); setConfirm(null);
    } catch (reason) { setVersionError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function inspect(file: File) {
    if (!release) return;
    setBusy(true); setImportError("");
    try {
      const form = new FormData(); form.append("file", file);
      const result = await knowledgeApi<ImportPreview>(token, `/releases/${release.id}/import-preview`, { method: "POST", body: form });
      setImportFile(file); setPreview(result); setImportFilter("all");
      setSelectedRows(result.rows.filter(row => row.kind === "added" || row.kind === "updated").map(row => row.source_row));
    } catch (reason) {
      setImportError(errorText(reason));
      setPreview({ source_name: file.name, checksum: "", draft_checksum: "", warnings: [], counts: { added: 0, updated: 0, unchanged: 0, conflict: 0 }, rows: [] });
    } finally { setBusy(false); }
  }

  function selectRows(rows: number[]) {
    if (!preview) return;
    let next = rows;
    const latest = rows.find(row => !selectedRows.includes(row));
    if (latest !== undefined) {
      const current = preview.rows.find(row => row.source_row === latest);
      if (current?.kind === "conflict") next = rows.filter(row => row === latest || !current.conflict_rows.includes(row));
    }
    setSelectedRows(next);
  }

  async function applyImport() {
    if (!release || !preview || !importFile || !selectedRows.length) return;
    setBusy(true); setImportError("");
    try {
      const form = new FormData();
      form.append("file", importFile); form.append("checksum", preview.checksum);
      form.append("draft_checksum", preview.draft_checksum);
      form.append("selected_rows", JSON.stringify(selectedRows));
      const result = await knowledgeApi<{ added: number; updated: number; unchanged: number }>(token, `/releases/${release.id}/import-apply`, { method: "POST", body: form });
      setPreview(null); setImportFile(null); setSelectedRows([]);
      setVersionNotice(`导入审核完成：新增 ${result.added}，更新 ${result.updated}，未变化 ${result.unchanged}。`); onChanged();
    } catch (reason) { setImportError(errorText(reason)); }
    finally { setBusy(false); }
  }

  const visibleRows = useMemo(() => preview?.rows.filter(row => importFilter === "all" || row.kind === importFilter) ?? [], [preview, importFilter]);
  return <>
    <section className="knowledge-section knowledge-workspace">
      <div className="knowledge-workspace-header">
      <div className="knowledge-workspace-topline">
        <div className="knowledge-release-select"><span>知识版本</span><Select aria-label="知识版本" disabled={busy || preview !== null} showSearch optionFilterProp="label" value={selectedReleaseId || undefined} placeholder="选择版本" onChange={id => { setVectorError(""); setVectorNotice(""); setVersionError(""); setVersionNotice(""); setCreateNotice(""); onSelectRelease(id); }}
          options={releases.map(item => ({ value: item.id, label: `${item.name} · ${labels[item.status]}` }))} /></div>
        <span className="knowledge-draft-count">草稿 {draftCount}/{draftLimit}</span>
      </div>
      <div className="knowledge-create-row">
        <Input aria-label="新版本名称" placeholder="新草稿名称（可选）" maxLength={120} value={name} disabled={!canManage || busy} onChange={event => setName(event.target.value)} />
        <Button type="primary" disabled={!canManage || !release || draftCount >= draftLimit} loading={busy} onClick={() => void create(true)}>复制当前版本</Button>
        <Button disabled={!canManage || busy || draftCount >= draftLimit} onClick={() => void create(false)}>新建空白版本</Button>
      </div>
      </div>
      <ErrorNotice error={createError} />
      {createNotice && <Alert className="knowledge-action-feedback" type="success" showIcon message={createNotice} closable onClose={() => setCreateNotice("")} />}
      {draftCount >= draftLimit && <p className="knowledge-limit-note">草稿已达上限，发布或删除草稿后可继续创建。</p>}
    {release && <div className="knowledge-release-tools">
      <div className="knowledge-vector-summary"><span><b>{release.entry_count.toLocaleString("zh-CN")}</b> 条知识</span><span><b>{release.vectorized_count.toLocaleString("zh-CN")}</b> 条已向量化</span><span><b>{release.pending_count.toLocaleString("zh-CN")}</b> 条待处理</span>
      <div className="knowledge-version-progress"><Progress percent={release.entry_count ? Math.round(release.vectorized_count / release.entry_count * 100) : 0} showInfo={false} /><span>{release.entry_count ? Math.round(release.vectorized_count / release.entry_count * 100) : 0}%</span></div></div>
      <div className="knowledge-version-actions">
        <div className="knowledge-vector-actions"><InputNumber aria-label="批次大小" min={1} max={256} value={batch} disabled={!canManage} onChange={value => setBatch(value ?? 32)} addonBefore="批次" />
          <Button disabled={!canManage || busy || !embeddingConfigured || release.pending_count === 0} onClick={() => { setVectorError(""); setConfirm("complete"); }}>补全缺失向量</Button>
          <Button disabled={!canManage || busy || !embeddingConfigured || release.status !== "draft"} onClick={() => { setVectorError(""); setConfirm("rebuild"); }}>全量重建</Button></div>
        <div className="knowledge-release-actions"><Upload accept=".xlsx" showUploadList={false} disabled={!canManage || busy || release.status !== "draft"} beforeUpload={file => { void inspect(file); return false; }}>
            <Button loading={busy} disabled={!canManage || release.status !== "draft"}>导入并审核</Button>
          </Upload>
          <Button disabled={!canManage || busy || !release.entry_count || release.status === "published"} onClick={() => { setVersionError(""); setConfirm("publish"); }}>{release.status === "archived" ? "回退版本" : "发布版本"}</Button>
          <Button danger type="text" disabled={!canManage || busy || release.status !== "draft"} onClick={() => { setVersionError(""); setConfirm("delete"); }}>删除草稿</Button></div>
      </div>
      <div className="knowledge-action-feedback">
        {!confirm && <ErrorNotice error={vectorError} />}
        {vectorNotice && <Alert type="success" showIcon message={vectorNotice} closable onClose={() => setVectorNotice("")} />}
      </div>
      <div className="knowledge-action-feedback">
        {!confirm && <ErrorNotice error={versionError} />}
        {versionNotice && <Alert type="success" showIcon message={versionNotice} closable onClose={() => setVersionNotice("")} />}
      </div>
      {!preview && <ErrorNotice error={importError} />}
    </div>}
    </section>
    <Drawer className="import-review-drawer" title="审核导入内容" open={preview !== null} closable={!busy} maskClosable={!busy} keyboard={!busy} onClose={() => { if (!busy) { setPreview(null); setImportFile(null); setImportError(""); } }} width={880}
      extra={<Button type="primary" loading={busy} disabled={!canManage || busy || !selectedRows.length || !preview?.checksum} onClick={() => void applyImport()}>合并已选 {selectedRows.length} 条</Button>}>
      {preview && <>
        <div className="import-summary">
          {(["added", "updated", "unchanged", "conflict"] as const).map(kind => <button type="button" key={kind} onClick={() => setImportFilter(kind)}><strong>{preview.counts[kind]}</strong><span>{kindLabels[kind]}</span></button>)}
        </div>
        <p className="muted">{preview.source_name} · 默认选择新增和更新项；文件内重复项请人工选择一条，未变化项无需合并。</p>
        {preview.warnings.map(warning => <Alert key={warning} type="warning" showIcon message={warning} />)}
        <ErrorNotice error={importError} />
        <div className="import-review-toolbar"><Select value={importFilter} onChange={setImportFilter} options={[
          { value: "all", label: `全部（${preview.rows.length}）` },
          ...(["added", "updated", "unchanged", "conflict"] as const).map(kind => ({ value: kind, label: `${kindLabels[kind]}（${preview.counts[kind]}）` })),
        ]} /><span>勾选要合并到当前草稿的内容</span></div>
        <Table<ImportPreviewRow> rowKey="source_row" size="small" dataSource={visibleRows} pagination={{ pageSize: 12 }} scroll={{ x: 720 }}
          rowSelection={{ selectedRowKeys: selectedRows, onChange: keys => selectRows(keys.map(Number)), getCheckboxProps: row => ({ disabled: busy || !canManage || row.kind === "unchanged" }) }} columns={[
            { title: "状态", dataIndex: "kind", width: 110, render: (kind: ImportPreviewRow["kind"]) => <Tag color={kindColors[kind]}>{kindLabels[kind]}</Tag> },
            { title: "标准问题", dataIndex: ["incoming", "question"], ellipsis: true },
            { title: "分类", dataIndex: ["incoming", "category"], width: 120 },
            { title: "来源行", dataIndex: "source_row", width: 85 },
            { title: "审核", width: 90, render: (_, row) => <Button type="link" onClick={() => setComparing(row)}>{row.existing ? "对比" : "查看"}</Button> },
          ]} />
      </>}
    </Drawer>
    <Modal open={comparing !== null} title={comparing?.existing ? "新旧内容对比" : "新增内容"} footer={null} width={820} onCancel={() => setComparing(null)}>
      {comparing && <div className="import-compare">
        {Object.keys(fieldLabels).map(field => <section key={field} className={comparing.changed_fields.includes(field as keyof typeof comparing.incoming) ? "changed" : ""}>
          <h4>{fieldLabels[field]}{comparing.changed_fields.includes(field as keyof typeof comparing.incoming) && <Tag color="blue">有变化</Tag>}</h4>
          {comparing.existing && <div><span>当前内容</span><p>{String(comparing.existing[field as keyof typeof comparing.existing] || "—")}</p></div>}
          <div><span>导入内容</span><p>{String(comparing.incoming[field as keyof typeof comparing.incoming] || "—")}</p></div>
        </section>)}
      </div>}
    </Modal>
    <Modal open={!!confirm} title={confirm === "delete" ? "删除草稿" : confirm === "rebuild" ? "确认全量重建" : confirm === "complete" ? "补全缺失向量" : "确认发布版本"} onCancel={() => { if (!busy) setConfirm(null); }} onOk={() => void confirmed()} confirmLoading={busy} closable={!busy} maskClosable={!busy} okButtonProps={{ disabled: !canManage }} okText="确认" cancelText="取消">
      <p>{release?.name}</p>
      <p>{confirm === "delete" ? "将删除此草稿及全部知识，已发布和历史版本仍保留。" : confirm === "rebuild" ? "重新计算当前草稿的所有向量，会调用已配置的 Embedding 服务。" : confirm === "complete" ? `将为 ${release?.pending_count ?? 0} 条知识生成向量，每批 ${batch} 条，会调用已配置的 Embedding 服务。` : `发布后患者端立即使用此版本；当前有 ${release?.pending_count ?? 0} 条缺少向量。`}</p>
      <ErrorNotice error={confirm === "complete" || confirm === "rebuild" ? vectorError : versionError} />
    </Modal>
  </>;
}
