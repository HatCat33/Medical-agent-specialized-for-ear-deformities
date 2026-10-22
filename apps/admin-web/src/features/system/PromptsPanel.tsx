import { Alert, Button, Input, Modal, Spin, Table, Tag } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { type PromptInventory, type PromptRelease, systemApi } from "../../lib/api";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText, labels } from "../knowledge/shared";

export function PromptsPanel({ token }: { token: string }) {
  const [inventory, setInventory] = useState<PromptInventory | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [systemContent, setSystemContent] = useState("");
  const selectionRef = useRef("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [confirm, setConfirm] = useState<{ row: PromptRelease; action: "publish" | "delete" } | null>(null);
  const selected = useMemo(
    () => inventory?.items.find(item => item.id === selectedId) ?? null,
    [inventory, selectedId],
  );

  useEffect(() => {
    let active = true;
    void systemApi<PromptInventory>(token, "/prompts")
      .then(data => {
        if (!active) return;
        setInventory(data);
        setError("");
        const next = data.items.find(item => item.id === selectionRef.current)
          ?? data.items.find(item => item.status === "draft")
          ?? data.effective;
        if (next) {
          selectionRef.current = next.id;
          setSelectedId(next.id); setName(next.name); setContent(next.content); setSystemContent(next.system_content);
        } else {
          setSelectedId(""); setName(""); setContent(data.effective_content); setSystemContent(data.effective_system_content);
        }
      })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); });
    return () => { active = false; };
  }, [token, revision]);

  function choose(row: PromptRelease) {
    selectionRef.current = row.id;
    setSystemContent(row.system_content);
    setSelectedId(row.id); setName(row.name); setContent(row.content); setError("");
  }

  async function createDraft() {
    if (!inventory) return;
    setBusy(true); setError("");
    try {
      const row = await systemApi<PromptRelease>(token, "/prompts", {
        method: "POST",
        body: JSON.stringify({
          name: `系统提示词草稿 ${new Date().toLocaleString("zh-CN", { hour12: false })}`,
          content: inventory.effective_content,
          system_content: inventory.effective_system_content,
        }),
      });
      selectionRef.current = row.id;
      setSelectedId(row.id); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!selected || selected.status !== "draft") return;
    setBusy(true); setError("");
    try {
      await systemApi(token, `/prompts/${selected.id}`, {
        method: "PUT", body: JSON.stringify({ name, content, system_content: systemContent }),
      });
      setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function confirmOperation() {
    if (!confirm) return;
    setBusy(true); setError("");
    try {
      if (confirm.action === "publish") {
        await systemApi(token, `/prompts/${confirm.row.id}/publish`, { method: "POST" });
        setSelectedId(confirm.row.id);
        selectionRef.current = confirm.row.id;
      } else {
        await systemApi(token, `/prompts/${confirm.row.id}`, { method: "DELETE" });
        setSelectedId("");
        selectionRef.current = "";
      }
      setRevision(value => value + 1);
      setConfirm(null);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  if (!inventory && !error) return <Spin />;
  return <>
    <ErrorNotice error={error} />
    {inventory && <>
      <Alert type="info" showIcon message={inventory.using_default ? "当前使用代码内置默认提示词。创建草稿并发布后才会影响患者问答。" : `当前已发布：${inventory.effective?.name ?? ""}`} />
      <section className="knowledge-section">
        <div className="section-heading">
          <div><h2>提示词版本</h2><span>草稿保存不会影响患者端，只有发布操作会切换运行版本</span></div>
          <Button type="primary" loading={busy} onClick={() => void createDraft()}>基于当前版本创建草稿</Button>
        </div>
        <Table<PromptRelease> rowKey="id" size="small" pagination={{ pageSize: 6 }} dataSource={inventory.items} columns={[
          { title: "版本名称", dataIndex: "name", render: (value: string, row) => <Button type="link" className="table-link" onClick={() => choose(row)}>{value}</Button> },
          { title: "状态", dataIndex: "status", render: (value: string) => <Tag color={value === "published" ? "green" : value === "draft" ? "blue" : "default"}>{labels[value] ?? value}</Tag> },
          { title: "校验值", dataIndex: "checksum", responsive: ["md"], render: (value: string) => <code>{value.slice(0, 12)}</code> },
          { title: "发布时间", dataIndex: "published_at", responsive: ["md"], render: (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—" },
          { title: "操作", render: (_, row) => <div className="inline-actions">
            {row.status !== "published" && <Button size="small" onClick={() => setConfirm({ row, action: "publish" })}>{row.status === "archived" ? "回退" : "发布"}</Button>}
            {row.status === "draft" && <Button size="small" danger onClick={() => setConfirm({ row, action: "delete" })}>删除</Button>}
          </div> },
        ]} />
      </section>
      <section className="knowledge-section prompt-editor">
        <div className="section-heading"><div><h2>版本内容</h2><span>{selected ? `${selected.name} · ${labels[selected.status] ?? selected.status}` : "创建草稿后编辑"}</span></div>
          <Button type="primary" disabled={!selected || selected.status !== "draft" || !name.trim() || !content.trim() || !systemContent.trim()} loading={busy} onClick={() => void save()}>保存草稿</Button>
        </div>
        <label>版本名称<Input aria-label="版本名称" value={name} disabled={!selected || selected.status !== "draft"} onChange={event => setName(event.target.value)} maxLength={120} /></label>
        <label>系统提示词<Input.TextArea aria-label="系统提示词内容" value={systemContent} disabled={!selected || selected.status !== "draft"} onChange={event => setSystemContent(event.target.value)} autoSize={{ minRows: 7, maxRows: 18 }} maxLength={50_000} showCount /></label>
        <label>业务提示词<Input.TextArea aria-label="提示词内容" value={content} disabled={!selected || selected.status !== "draft"} onChange={event => setContent(event.target.value)} autoSize={{ minRows: 5, maxRows: 16 }} maxLength={50_000} showCount /></label>
      </section>
      <Modal open={confirm !== null}
        title={confirm?.action === "delete" ? "删除提示词草稿？" : confirm?.row.status === "archived" ? "回退到这个提示词版本？" : "发布这个提示词版本？"}
        okText={confirm?.action === "delete" ? "删除" : confirm?.row.status === "archived" ? "确认回退" : "确认发布"}
        okButtonProps={{ danger: confirm?.action === "delete" }} cancelText="取消" confirmLoading={busy}
        onCancel={() => setConfirm(null)} onOk={() => void confirmOperation()}>
        <p>{confirm?.action === "delete" ? "删除后无法恢复。" : "系统提示词与业务提示词将一同发布，后续问答立即生效。请确认医疗安全约束完整；独立的风险规则仍然执行。"}</p>
      </Modal>
    </>}
  </>;
}
