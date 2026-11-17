import { Alert, Button, Input, Modal, Spin } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { type PromptInventory, type PromptRelease, systemApi } from "../../lib/api";
import { RecordNavigator } from "./RecordNavigator";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText, labels } from "../knowledge/shared";

export function PromptsPanel({ token, canManage }: { token: string; canManage: boolean }) {
  const [inventory, setInventory] = useState<PromptInventory | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [systemContent, setSystemContent] = useState("");
  const selectionRef = useRef("");
  const [actionBusy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(true);
  const busy = actionBusy || refreshing;
  const [error, setError] = useState("");
  const [errorScope, setErrorScope] = useState("load");
  const [notice, setNotice] = useState("");
  const [pendingSelection, setPendingSelection] = useState<PromptRelease | null>(null);
  const [revision, setRevision] = useState(0);
  const [confirm, setConfirm] = useState<{ row: PromptRelease; action: "publish" | "delete" } | null>(null);
  const selected = useMemo(
    () => inventory?.items.find(item => item.id === selectedId) ?? null,
    [inventory, selectedId],
  );

  useEffect(() => { if (!canManage) setConfirm(null); }, [canManage]);

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
      .catch((reason: unknown) => { if (active) { setErrorScope("load"); setError(errorText(reason)); } })
      .finally(() => { if (active) setRefreshing(false); });
    return () => { active = false; };
  }, [token, revision]);

  function choose(row: PromptRelease) {
    selectionRef.current = row.id;
    setNotice("");
    setSystemContent(row.system_content);
    setSelectedId(row.id); setName(row.name); setContent(row.content); setError("");
  }

  const dirty = !!selected && selected.status === "draft" && (name !== selected.name || content !== selected.content || systemContent !== selected.system_content);
  function requestSelection(id: string) {
    const row = inventory?.items.find(item => item.id === id);
    if (!row || row.id === selectedId) return;
    if (dirty) setPendingSelection(row); else choose(row);
  }

  async function createDraft() {
    if (!inventory) return;
    setBusy(true); setErrorScope("create"); setError(""); setNotice("");
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
      setSelectedId(row.id); setRefreshing(true); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!selected || selected.status !== "draft") return;
    setBusy(true); setErrorScope("save"); setError(""); setNotice("");
    try {
      await systemApi(token, `/prompts/${selected.id}`, {
        method: "PUT", body: JSON.stringify({ name, content, system_content: systemContent }),
      });
      setNotice("草稿已保存，发布后生效。"); setRefreshing(true); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function confirmOperation() {
    if (!confirm) return;
    setBusy(true); setErrorScope("confirm"); setError(""); setNotice("");
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
      setRefreshing(true); setRevision(value => value + 1);
      setNotice(confirm.action === "delete" ? "草稿已删除。" : "提示词已发布。"); setConfirm(null);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  if (!inventory && !error) return <Spin />;
  return <>
    {errorScope === "load" && error && <div className="admin-load-feedback"><ErrorNotice error={error} /><Button loading={refreshing} onClick={() => { setRefreshing(true); setRevision(value => value + 1); }}>重新加载</Button></div>}
    {inventory && <div className="record-workbench">
      <RecordNavigator title="提示词版本" items={inventory.items.map(row => ({ id: row.id, title: row.name, status: row.status, date: row.published_at ?? row.created_at }))}
        selectedId={selectedId} disabled={busy} onSelect={requestSelection}
        action={<Button size="small" disabled={!canManage || busy || dirty || inventory.draft_count >= inventory.draft_limit} onClick={() => void createDraft()}>新建草稿</Button>}
        feedback={<><p className="record-nav-note">草稿 {inventory.draft_count}/{inventory.draft_limit}</p>{errorScope === "create" && <ErrorNotice error={error} />}</>} />
      <section className="record-editor prompt-editor">
        <div className="record-editor-heading"><div><h2>{selected ? "提示词内容" : "默认提示词"}</h2>
          <span>{selected ? labels[selected.status] : "内置默认"}{dirty ? " · 有未保存修改" : ""}</span></div>
          <div className="inline-actions">
            <Button type="primary" disabled={!canManage || busy || !selected || selected.status !== "draft" || !name.trim() || !content.trim() || !systemContent.trim()} loading={busy && errorScope === "save"} onClick={() => void save()}>保存草稿</Button>
            {selected && selected.status !== "published" && <Button disabled={!canManage || busy || dirty} onClick={() => { setError(""); setConfirm({ row: selected, action: "publish" }); }}>{selected.status === "archived" ? "回退版本" : "发布"}</Button>}
            {selected?.status === "draft" && <Button type="text" danger disabled={!canManage || busy} onClick={() => { setError(""); setConfirm({ row: selected, action: "delete" }); }}>删除</Button>}
          </div>
        </div>
        {errorScope === "save" && <ErrorNotice error={error} />}
        {notice && <Alert type="success" showIcon message={notice} closable onClose={() => setNotice("")} />}
        <div className="prompt-meta-row"><label>版本名称<Input aria-label="版本名称" value={name} disabled={!canManage || busy || !selected || selected.status !== "draft"} onChange={event => setName(event.target.value)} maxLength={120} /></label><span className="prompt-effective">{inventory.using_default ? "内置默认" : `当前生效：${inventory.effective?.name ?? ""}`}</span></div>
        <div className="prompt-compose-grid">
          <div className="prompt-field"><div className="prompt-field-heading"><strong>系统提示词</strong><span>安全约束与通用规则</span></div><Input.TextArea aria-label="系统提示词内容" className="prompt-textarea" value={systemContent} disabled={!canManage || busy || !selected || selected.status !== "draft"} onChange={event => setSystemContent(event.target.value)} rows={16} maxLength={50_000} showCount /></div>
          <div className="prompt-field"><div className="prompt-field-heading"><strong>业务提示词</strong><span>专科表达与回答要求</span></div><Input.TextArea aria-label="提示词内容" className="prompt-textarea" value={content} disabled={!canManage || busy || !selected || selected.status !== "draft"} onChange={event => setContent(event.target.value)} rows={16} maxLength={50_000} showCount /></div>
        </div>
      </section>
    </div>}
    <Modal open={confirm !== null} title={confirm?.action === "delete" ? "删除提示词草稿？" : confirm?.row.status === "archived" ? "回退提示词版本？" : "发布提示词版本？"}
      okText={confirm?.action === "delete" ? "删除" : "确认发布"} okButtonProps={{ danger: confirm?.action === "delete", disabled: !canManage }}
      cancelText="取消" confirmLoading={busy} closable={!busy} maskClosable={!busy}
      onCancel={() => { if (!busy) setConfirm(null); }} onOk={() => void confirmOperation()}>
      <p>{confirm?.row.name}</p>
      <p>{confirm?.action === "delete" ? "删除后无法恢复。" : "系统提示词与业务提示词将一同发布，后续问答立即生效。请确认医疗安全约束完整。"}</p>
      {errorScope === "confirm" && <ErrorNotice error={error} />}
    </Modal>
    <Modal open={pendingSelection !== null} title="切换前放弃未保存的修改？" okText="放弃并切换" cancelText="继续编辑"
      onCancel={() => setPendingSelection(null)} onOk={() => { if (pendingSelection) choose(pendingSelection); setPendingSelection(null); }}>
      <p>当前草稿的修改尚未保存。</p>
    </Modal>
  </>;
}
