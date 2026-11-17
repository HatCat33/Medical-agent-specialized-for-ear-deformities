import { Alert, Button, Drawer, Form, Input, Modal, Select, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { knowledgeApi, type Entry, type EntryEdit, type Release } from "../../lib/api";
import { errorText, useAdminData } from "./shared";
import { ErrorNotice } from "./ErrorNotice";

export function EntriesPanel({ token, release, canManage, onChanged }: { token: string; release: Release; canManage: boolean; onChanged: () => void }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [vector, setVector] = useState("all");
  const [category, setCategory] = useState("");
  const [searchMode, setSearchMode] = useState("text");
  const [detail, setDetail] = useState<Entry | null>(null);
  const [editing, setEditing] = useState<Entry | "new" | null>(null);
  const [deleting, setDeleting] = useState<Entry | null>(null);
  const [busy, setBusy] = useState(false);
  const [editError, setEditError] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [actionNotice, setActionNotice] = useState("");
  const [form] = Form.useForm<EntryEdit>();
  const data = useAdminData<{ items: Entry[]; total: number; categories: string[] }>(token,
    `/releases/${release.id}/entries?${new URLSearchParams({ page: String(page), q, vector, category, search_mode: searchMode })}`);
  const editable = canManage && release.status === "draft";
  useEffect(() => {
    if (!canManage) { setEditing(null); setDeleting(null); }
  }, [canManage]);
  function edit(entry: Entry | "new") {
    setEditError("");
    form.resetFields();
    if (entry !== "new") form.setFieldsValue(entry);
    setEditing(entry);
  }
  async function save(values: EntryEdit) {
    setBusy(true); setEditError("");
    try {
      await knowledgeApi(token, `/releases/${release.id}/entries${editing && editing !== "new" ? `/${editing.id}` : ""}`, {
        method: editing === "new" ? "POST" : "PUT", body: JSON.stringify(values),
      }); setEditing(null); setActionNotice(editing === "new" ? "知识条目已新增。" : "知识条目已保存。"); onChanged();
    } catch (e) { setEditError(errorText(e)); } finally { setBusy(false); }
  }
  async function remove() {
    if (!deleting) return;
    setBusy(true); setDeleteError("");
    try { await knowledgeApi(token, `/releases/${release.id}/entries/${deleting.id}`, { method: "DELETE" }); setDeleting(null); setActionNotice("知识条目已删除。"); onChanged(); }
    catch (e) { setDeleteError(errorText(e)); } finally { setBusy(false); }
  }
  return <>
    {!editable && <Alert type="info" showIcon message={release.status === "draft" ? "当前处于只读状态，完成二次验证后可编辑草稿。" : "当前版本只读。请在本页上方复制为草稿，修改完成后再发布。"} />}
    <ErrorNotice error={data.error} />
    {actionNotice && <Alert className="knowledge-action-feedback" type="success" showIcon message={actionNotice} closable onClose={() => setActionNotice("")} />}
    <div className="knowledge-entry-toolbar">
      <Input.Search aria-label="搜索知识" placeholder="输入关键词，可用空格组合多个词" onSearch={value => { setQ(value); setPage(1); }} allowClear />
      <Select className="knowledge-search-mode" aria-label="搜索方式" value={searchMode} onChange={value => { setSearchMode(value); setPage(1); }} options={[
        { value: "text", label: "快速搜索" }, { value: "semantic", label: "智能搜索" },
      ]} />
      <Select className="knowledge-category-filter" aria-label="分类筛选" value={category} onChange={value => { setCategory(value); setPage(1); }} options={[
        { value: "", label: "全部分类" }, ...(data.data?.categories ?? []).map(value => ({ value, label: value })),
      ]} />
      <Select className="knowledge-vector-filter" aria-label="向量状态" value={vector} onChange={value => { setVector(value); setPage(1); }} options={[
        { value: "all", label: "全部状态" }, { value: "pending", label: "待向量化" }, { value: "ready", label: "已向量化" },
      ]} />
      <Button type="primary" disabled={!editable} onClick={() => edit("new")}>新增条目</Button>
    </div>
    {searchMode === "semantic" && <p className="muted">智能搜索会在提交关键词后调用一次已配置的 Embedding 服务；快速搜索不会产生模型调用。</p>}
    <div className="knowledge-entry-result-count">找到 {data.data?.total ?? 0} 条知识</div>
    <Table<Entry> className="knowledge-entry-table" rowKey="id" dataSource={data.data?.items} loading={!data.data && !data.error} scroll={{ x: 1000 }}
      pagination={{ current: page, pageSize: 10, total: data.data?.total, onChange: setPage, showSizeChanger: false, showTotal: total => `共 ${total} 条` }} columns={[
        { title: "问题 / 内容预览", key: "content", width: 350, render: (_, entry) => <div><Button type="link" className="chunk-title" onClick={() => setDetail(entry)}>{entry.question}</Button><p className="chunk-preview">{entry.answer}</p></div> },
        { title: "分类", dataIndex: "category", width: 110 },
        { title: "向量", dataIndex: "vectorized", width: 100, render: (v: boolean) => <Tag color={v ? "green" : "orange"}>{v ? "已就绪" : "待构建"}</Tag> },
        { title: "操作", key: "actions", width: 130, render: (_, e) => <div className="inline-actions"><Button size="small" disabled={!editable} onClick={() => edit(e)}>编辑</Button><Button size="small" danger type="text" disabled={!editable} onClick={() => setDeleting(e)}>删除</Button></div> },
      ]} />
    <Drawer title="Chunk 完整内容" open={!!detail} onClose={() => setDetail(null)} width={650}>
      {detail && <><div className="chunk-id-field"><strong>Chunk ID</strong><Typography.Text className="mono" copyable={{ text: detail.id, tooltips: ["复制 Chunk ID", "已复制"] }}>{detail.id}</Typography.Text></div><p>{detail.category} · {detail.source_sheet} · 第 {detail.source_row} 行</p><h3>实际向量化文本</h3><pre className="content-preview">{detail.chunk_text}</pre><p>有效日期：{detail.effective_date || "未指定"}</p></>}
    </Drawer>
    <Modal title={editing === "new" ? "新增条目" : "编辑条目"} open={!!editing} onCancel={() => setEditing(null)} onOk={() => form.submit()} confirmLoading={busy} okText="保存" cancelText="取消" width={700}>
      <ErrorNotice error={editError} />
      <Form form={form} layout="vertical" onFinish={values => void save(values)} initialValues={{ category: "", question: "", aliases: "", answer: "", effective_date: "" }}>
        <Form.Item label="分类" name="category" rules={[{ required: true, whitespace: true }]}><Input maxLength={120} /></Form.Item>
        <Form.Item label="标准问题" name="question" rules={[{ required: true, whitespace: true }]}><Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} maxLength={4000} /></Form.Item>
        <Form.Item label="其他问法" name="aliases"><Input.TextArea autoSize maxLength={8000} /></Form.Item>
        <Form.Item label="答案内容" name="answer" rules={[{ required: true, whitespace: true }]}><Input.TextArea autoSize={{ minRows: 5, maxRows: 12 }} maxLength={50000} /></Form.Item>
        <Form.Item label="有效日期" name="effective_date"><Input maxLength={80} /></Form.Item>
      </Form><p className="muted">修改问题、其他问法或答案后，该条旧向量会失效，需要补全向量。</p>
    </Modal>
    <Modal title="确认删除 chunk" open={!!deleting} onCancel={() => setDeleting(null)} onOk={() => void remove()} confirmLoading={busy} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
      <ErrorNotice error={deleteError} /><p>从草稿中删除“{deleting?.question}”及其向量。发布后该条将不再参与患者端检索。</p>
    </Modal>
  </>;
}
