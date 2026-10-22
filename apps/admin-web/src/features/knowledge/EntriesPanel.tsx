import { Alert, Button, Drawer, Form, Input, Modal, Select, Table, Tag, Typography } from "antd";
import { useState } from "react";
import { knowledgeApi, type Entry, type EntryEdit, type Release } from "../../lib/api";
import { errorText, useAdminData } from "./shared";
import { ErrorNotice } from "./ErrorNotice";

export function EntriesPanel({ token, release, onChanged }: { token: string; release: Release; onChanged: () => void }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [vector, setVector] = useState("all");
  const [detail, setDetail] = useState<Entry | null>(null);
  const [editing, setEditing] = useState<Entry | "new" | null>(null);
  const [deleting, setDeleting] = useState<Entry | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form] = Form.useForm<EntryEdit>();
  const data = useAdminData<{ items: Entry[]; total: number }>(token,
    `/releases/${release.id}/entries?${new URLSearchParams({ page: String(page), q, vector })}`);
  const editable = release.status === "draft";
  function edit(entry: Entry | "new") {
    form.resetFields();
    if (entry !== "new") form.setFieldsValue(entry);
    setEditing(entry);
  }
  async function save(values: EntryEdit) {
    setBusy(true); setError("");
    try {
      await knowledgeApi(token, `/releases/${release.id}/entries${editing && editing !== "new" ? `/${editing.id}` : ""}`, {
        method: editing === "new" ? "POST" : "PUT", body: JSON.stringify(values),
      }); setEditing(null); onChanged();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function remove() {
    if (!deleting) return;
    setBusy(true); setError("");
    try { await knowledgeApi(token, `/releases/${release.id}/entries/${deleting.id}`, { method: "DELETE" }); setDeleting(null); onChanged(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <>
    {!editable && <Alert type="info" showIcon message="当前版本只读。请到知识版本复制为草稿，修改后发布。" />}
    <ErrorNotice error={error || data.error} />
    <div className="toolbar">
      <Input.Search aria-label="搜索分块" placeholder="搜索问题、答案、分类" onSearch={value => { setQ(value); setPage(1); }} allowClear />
      <Select aria-label="向量状态" value={vector} onChange={value => { setVector(value); setPage(1); }} options={[
        { value: "all", label: "全部状态" }, { value: "pending", label: "待向量化" }, { value: "ready", label: "已向量化" },
      ]} />
      <Button type="primary" disabled={!editable} onClick={() => edit("new")}>新增条目</Button>
    </div>
    <Table<Entry> rowKey="id" dataSource={data.data?.items} loading={!data.data && !data.error} scroll={{ x: 1000 }}
      pagination={{ current: page, pageSize: 20, total: data.data?.total, onChange: setPage, showSizeChanger: false }} columns={[
        { title: "问题 / 内容预览", key: "content", width: 350, render: (_, entry) => <div><Button type="link" className="chunk-title" onClick={() => setDetail(entry)}>{entry.question}</Button><p className="chunk-preview">{entry.answer}</p></div> },
        { title: "分类", dataIndex: "category", width: 110 },
        { title: "向量", dataIndex: "vectorized", width: 100, render: (v: boolean) => <Tag color={v ? "green" : "orange"}>{v ? "已就绪" : "待构建"}</Tag> },
        { title: "Chunk ID", dataIndex: "id", width: 220, render: (id: string) => <Typography.Text className="chunk-id" copyable={{ text: id, tooltips: ["复制 Chunk ID", "已复制"] }}>{id}</Typography.Text> },
        { title: "来源", key: "source", width: 160, render: (_, e) => <span>{e.source_sheet} · 第 {e.source_row} 行</span> },
        { title: "操作", key: "actions", width: 140, render: (_, e) => <div className="inline-actions"><Button size="small" disabled={!editable} onClick={() => edit(e)}>编辑</Button><Button size="small" danger disabled={!editable} onClick={() => setDeleting(e)}>删除</Button></div> },
      ]} />
    <Drawer title="Chunk 完整内容" open={!!detail} onClose={() => setDetail(null)} width={650}>
      {detail && <><div className="chunk-id-field"><strong>Chunk ID</strong><Typography.Text className="mono" copyable={{ text: detail.id, tooltips: ["复制 Chunk ID", "已复制"] }}>{detail.id}</Typography.Text></div><p>{detail.category} · {detail.source_sheet} · 第 {detail.source_row} 行</p><h3>实际向量化文本</h3><pre className="content-preview">{detail.chunk_text}</pre><p>有效日期：{detail.effective_date || "未指定"}</p></>}
    </Drawer>
    <Modal title={editing === "new" ? "新增条目" : "编辑条目"} open={!!editing} onCancel={() => setEditing(null)} onOk={() => form.submit()} confirmLoading={busy} okText="保存" cancelText="取消" width={700}>
      <ErrorNotice error={error} />
      <Form form={form} layout="vertical" onFinish={values => void save(values)} initialValues={{ category: "", question: "", aliases: "", answer: "", effective_date: "" }}>
        <Form.Item label="分类" name="category" rules={[{ required: true, whitespace: true }]}><Input maxLength={120} /></Form.Item>
        <Form.Item label="标准问题" name="question" rules={[{ required: true, whitespace: true }]}><Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} maxLength={4000} /></Form.Item>
        <Form.Item label="其他问法" name="aliases"><Input.TextArea autoSize maxLength={8000} /></Form.Item>
        <Form.Item label="答案内容" name="answer" rules={[{ required: true, whitespace: true }]}><Input.TextArea autoSize={{ minRows: 5, maxRows: 12 }} maxLength={50000} /></Form.Item>
        <Form.Item label="有效日期" name="effective_date"><Input maxLength={80} /></Form.Item>
      </Form><p className="muted">修改问题、其他问法或答案后，该条旧向量会失效，需要补全向量。</p>
    </Modal>
    <Modal title="确认删除 chunk" open={!!deleting} onCancel={() => setDeleting(null)} onOk={() => void remove()} confirmLoading={busy} okText="删除" cancelText="取消" okButtonProps={{ danger: true }}>
      <ErrorNotice error={error} /><p>从草稿中删除“{deleting?.question}”及其向量。发布后该条将不再参与患者端检索。</p>
    </Modal>
  </>;
}
