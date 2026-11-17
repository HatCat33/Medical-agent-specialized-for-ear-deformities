import { Alert, Button, Input, Modal, Spin } from "antd";
import MDEditor from "@uiw/react-md-editor";
import "@uiw/react-md-editor/markdown-editor.css";
import { useEffect, useMemo, useRef, useState } from "react";

import { type Announcement, type AnnouncementInventory, announcementApi } from "../../lib/api";
import { RecordNavigator } from "./RecordNavigator";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText } from "../knowledge/shared";

export function AnnouncementsPanel({ token, canManage }: { token: string; canManage: boolean }) {
  const [items, setItems] = useState<Announcement[] | null>(null);
  const [draftCount, setDraftCount] = useState(0);
  const [draftLimit, setDraftLimit] = useState(3);
  const [selectedId, setSelectedId] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [actionBusy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(true);
  const busy = actionBusy || refreshing;
  const [error, setError] = useState("");
  const [errorScope, setErrorScope] = useState("load");
  const [notice, setNotice] = useState("");
  const [pendingSelection, setPendingSelection] = useState<Announcement | null>(null);
  const [revision, setRevision] = useState(0);
  const selectionRef = useRef("");
  const [confirm, setConfirm] = useState<{ row: Announcement; action: "publish" | "delete" } | null>(null);
  const selected = useMemo(
    () => items?.find(item => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  useEffect(() => { if (!canManage) setConfirm(null); }, [canManage]);

  useEffect(() => {
    let active = true;
    void announcementApi<AnnouncementInventory>(token)
      .then(inventory => {
        if (!active) return;
        const data = inventory.items;
        setDraftCount(inventory.draft_count); setDraftLimit(inventory.draft_limit);
        setItems(data); setError("");
        const next = data.find(item => item.id === selectionRef.current)
          ?? data.find(item => item.status === "draft") ?? data[0];
        if (next) choose(next);
        else { setSelectedId(""); setTitle(""); setContent(""); }
      })
      .catch((reason: unknown) => { if (active) { setErrorScope("load"); setError(errorText(reason)); } })
      .finally(() => { if (active) setRefreshing(false); });
    return () => { active = false; };
  }, [token, revision]);

  function choose(row: Announcement) {
    selectionRef.current = row.id;
    setSelectedId(row.id); setTitle(row.title); setContent(row.content); setError("");
  }

  const dirty = !!selected && selected.status === "draft" && (title !== selected.title || content !== selected.content);
  function requestSelection(id: string) {
    const row = items?.find(item => item.id === id);
    if (!row || row.id === selectedId) return;
    if (dirty) setPendingSelection(row); else { setNotice(""); choose(row); }
  }

  async function createDraft() {
    setBusy(true); setErrorScope("create"); setError(""); setNotice("");
    try {
      const row = await announcementApi<Announcement>(token, "", {
        method: "POST",
        body: JSON.stringify({ title: "新公告", content: "请填写本次更新或通知内容。" }),
      });
      selectionRef.current = row.id; setSelectedId(row.id); setRefreshing(true); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!selected || selected.status !== "draft") return;
    setBusy(true); setErrorScope("save"); setError(""); setNotice("");
    try {
      await announcementApi(token, `/${selected.id}`, {
        method: "PUT", body: JSON.stringify({ title, content }),
      });
      setNotice("公告草稿已保存。"); setRefreshing(true); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  async function confirmOperation() {
    if (!confirm) return;
    setBusy(true); setErrorScope("confirm"); setError(""); setNotice("");
    try {
      if (confirm.action === "publish") {
        await announcementApi(token, `/${confirm.row.id}/publish`, { method: "POST" });
      } else {
        await announcementApi(token, `/${confirm.row.id}`, { method: "DELETE" });
        selectionRef.current = ""; setSelectedId("");
      }
      setNotice(confirm.action === "delete" ? "公告草稿已删除。" : "公告已发布，将在患者下次进入时展示。"); setConfirm(null); setRefreshing(true); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  if (!items && !error) return <Spin />;
  return <>
    {errorScope === "load" && error && <div className="admin-load-feedback"><ErrorNotice error={error} /><Button loading={refreshing} onClick={() => { setRefreshing(true); setRevision(value => value + 1); }}>重新加载</Button></div>}
    <div className="record-workbench">
      <RecordNavigator title="公告记录" items={(items ?? []).map(row => ({ id: row.id, title: row.title, status: row.status, date: row.published_at ?? row.created_at }))}
        selectedId={selectedId} disabled={busy} onSelect={requestSelection}
        action={<Button size="small" disabled={!canManage || busy || dirty || !items || draftCount >= draftLimit} onClick={() => void createDraft()}>新建草稿</Button>}
        feedback={<><p className="record-nav-note">草稿 {draftCount}/{draftLimit}</p>{errorScope === "create" && <ErrorNotice error={error} />}</>} />
      <section className="record-editor announcement-editor">
        <div className="record-editor-heading"><div><h2>公告内容</h2><span>{selected ? selected.status === "published" ? "已发布 · 只读" : dirty ? "草稿 · 有未保存修改" : "草稿" : "创建草稿后开始编辑"}</span></div>
          <div className="inline-actions">
            <Button type="primary" loading={busy && errorScope === "save"} disabled={!canManage || busy || !selected || selected.status !== "draft" || !title.trim() || !content.trim()} onClick={() => void save()}>保存草稿</Button>
            {selected?.status === "draft" && <><Button disabled={!canManage || busy || dirty} onClick={() => { setError(""); setConfirm({ row: selected, action: "publish" }); }}>发布</Button>
              <Button type="text" danger disabled={!canManage || busy} onClick={() => { setError(""); setConfirm({ row: selected, action: "delete" }); }}>删除</Button></>}
          </div>
        </div>
        {errorScope === "save" && <ErrorNotice error={error} />}
        {notice && <Alert type="success" showIcon message={notice} closable onClose={() => setNotice("")} />}
        <div className="announcement-meta-row"><label>公告标题<Input value={title} maxLength={160} showCount disabled={!canManage || busy || !selected || selected.status !== "draft"} onChange={event => setTitle(event.target.value)} /></label><span>{dirty ? "有未保存修改" : selected?.status === "published" ? "已发布" : "草稿"}</span></div>
        <div className="announcement-md-editor" data-color-mode="light">
          <MDEditor
            value={content}
            onChange={value => setContent((value ?? "").slice(0, 20_000))}
            height={520}
            preview="live"
            visibleDragbar
            textareaProps={{
              disabled: !canManage || busy || !selected || selected.status !== "draft",
              maxLength: 20_000,
              placeholder: "## 本次更新\n\n- 新增功能\n- 优化体验",
              "aria-label": "公告正文",
            }}
          />
          <span className="announcement-character-count">{content.length} / 20000</span>
        </div>
      </section>
    </div>
    <Modal open={confirm !== null} title={confirm?.action === "delete" ? "删除公告草稿？" : "发布这条公告？"}
      okText={confirm?.action === "delete" ? "删除" : "确认发布"} cancelText="取消"
      okButtonProps={{ danger: confirm?.action === "delete", disabled: !canManage }} confirmLoading={busy} closable={!busy} maskClosable={!busy}
      onCancel={() => { if (!busy) setConfirm(null); }} onOk={() => void confirmOperation()}>
      <p>{confirm?.row.title}</p><p>{confirm?.action === "delete" ? "删除后无法恢复。" : "发布后患者下次进入时会看到这条公告，且公告内容将保持只读。"}</p>
      {errorScope === "confirm" && <ErrorNotice error={error} />}
    </Modal>
    <Modal open={pendingSelection !== null} title="切换前放弃未保存的修改？" okText="放弃并切换" cancelText="继续编辑"
      onCancel={() => setPendingSelection(null)} onOk={() => { if (pendingSelection) { setNotice(""); choose(pendingSelection); } setPendingSelection(null); }}>
      <p>当前公告的修改尚未保存。</p>
    </Modal>
  </>;
}
