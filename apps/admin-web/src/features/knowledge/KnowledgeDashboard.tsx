import { Button, Empty, Input, InputNumber, Modal, Select, Spin, Tabs, Tag } from "antd";
import {
  Activity,
  BookOpenCheck,
  MessageSquareText,
  Settings2,
} from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { ApiError, type Overview, type Release, type RetrievalConfiguration, type SecondarySecurityStatus, systemApi, verifySecondaryToken } from "../../lib/api";
import { errorText, labels, useAdminData } from "./shared";
import { ErrorNotice } from "./ErrorNotice";
import { type AdminSection, sectionNames } from "./navigation";

const EntriesPanel = lazy(() => import("./EntriesPanel").then(m => ({ default: m.EntriesPanel })));
const RetrievalPanel = lazy(() => import("./RetrievalPanel").then(m => ({ default: m.RetrievalPanel })));
const VersionsPanel = lazy(() => import("./VersionsPanel").then(m => ({ default: m.VersionsPanel })));
const PromptsPanel = lazy(() => import("../system/PromptsPanel").then(m => ({ default: m.PromptsPanel })));
const ModelsPanel = lazy(() => import("../system/ModelsPanel").then(m => ({ default: m.ModelsPanel })));
const CapacityPanel = lazy(() => import("../system/CapacityPanel").then(m => ({ default: m.CapacityPanel })));
const AnnouncementsPanel = lazy(() => import("../system/AnnouncementsPanel").then(m => ({ default: m.AnnouncementsPanel })));
const InteractionAnalyticsPanel = lazy(() => import("./InteractionAnalyticsPanel").then(m => ({ default: m.InteractionAnalyticsPanel })));

const subtitles: Record<AdminSection, string> = {
  overview: "掌握患者使用、知识检索与模型服务运行情况",
  knowledge: "版本、导入、向量与知识内容统一管理",
  tests: "单条检索验证与批量效果评测",
  prompts: "系统与业务提示词的版本发布",
  announcements: "发布患者端功能更新与服务通知",
};

function compactCount(value: number): string {
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}w+`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k+`;
  return value.toLocaleString("zh-CN");
}

function OverviewContent({ token, revision, overview, releases, onChanged, security, authorizationExpiry, onSecurityChanged }: {
  token: string;
  revision: number;
  overview: Overview;
  releases: Release[];
  onChanged: () => void;
  security: SecondarySecurityStatus | null;
  authorizationExpiry: string;
  onSecurityChanged: (status: SecondarySecurityStatus) => void;
}) {
  const canManage = security?.verified ?? false;
  const publishedEntries = releases.find(item => item.status === "published")?.entry_count ?? 0;
  const [editingRetrieval, setEditingRetrieval] = useState(false);
  const [savingRetrieval, setSavingRetrieval] = useState(false);
  const [retrievalError, setRetrievalError] = useState("");
  const [secondaryToken, setSecondaryToken] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [verificationError, setVerificationError] = useState("");
  const [retrievalDraft, setRetrievalDraft] = useState<RetrievalConfiguration>({
    top_k: overview.rag_top_k,
    min_score: overview.rag_min_score,
    relative_score: overview.rag_relative_score,
  });

  function openRetrievalSettings() {
    setRetrievalDraft({
      top_k: overview.rag_top_k,
      min_score: overview.rag_min_score,
      relative_score: overview.rag_relative_score,
    });
    setRetrievalError("");
    setEditingRetrieval(true);
  }

  async function verifySecondary() {
    if (!secondaryToken.trim() || !security?.configured) return;
    setVerifying(true);
    setVerificationError("");
    try {
      await verifySecondaryToken(token, secondaryToken.trim());
      setSecondaryToken("");
      onSecurityChanged({ ...security, verified: true });
    } catch (reason) {
      setVerificationError(reason instanceof ApiError ? reason.message : "二次验证暂时不可用");
    } finally {
      setVerifying(false);
    }
  }

  async function saveRetrievalSettings() {
    setSavingRetrieval(true);
    setRetrievalError("");
    try {
      await systemApi<RetrievalConfiguration>(token, "/retrieval/configuration", {
        method: "PUT",
        body: JSON.stringify(retrievalDraft),
      });
      setEditingRetrieval(false);
      onChanged();
    } catch (reason) {
      setRetrievalError(errorText(reason));
    } finally {
      setSavingRetrieval(false);
    }
  }

  async function resetRetrievalSettings() {
    setSavingRetrieval(true);
    setRetrievalError("");
    try {
      await systemApi<RetrievalConfiguration>(token, "/retrieval/configuration", {
        method: "DELETE",
      });
      setEditingRetrieval(false);
      onChanged();
    } catch (reason) {
      setRetrievalError(errorText(reason));
    } finally {
      setSavingRetrieval(false);
    }
  }

  return <>
    <section className="overview-summary">
      <div className="overview-summary-metrics">
        <div title="每个真实患者提问累计一次，删除会话后也不会减少"><MessageSquareText size={18} /><span>累计交互</span><strong>{compactCount(overview.interaction_count ?? 0)}</strong></div>
        <div><BookOpenCheck size={18} /><span>当前知识</span><strong>{publishedEntries.toLocaleString("zh-CN")}</strong></div>
        <div><Activity size={18} /><span>运行任务</span><strong>{overview.active_jobs}</strong></div>
      </div>
      <div className="overview-retrieval-row">
        <div className="overview-secondary-control">
          <Input.Password
            value={secondaryToken}
            disabled={!security?.configured || security.verified}
            placeholder={!security?.configured ? "未配置二次验证令牌" : security.verified && authorizationExpiry ? `已验证至 ${new Date(authorizationExpiry).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}` : "输入二次验证令牌"}
            autoComplete="off"
            status={verificationError ? "error" : undefined}
            onChange={event => setSecondaryToken(event.target.value)}
            onPressEnter={() => void verifySecondary()}
          />
          <Button
            type={security?.verified ? "primary" : "default"}
            className={security?.verified ? "secondary-verified-button" : ""}
            loading={verifying}
            disabled={!security?.configured || security.verified || !secondaryToken.trim()}
            onClick={() => void verifySecondary()}
          >{security?.verified ? "已验证" : "二次验证"}</Button>
          {verificationError && <span className="overview-secondary-error">{verificationError}</span>}
        </div>
        <div className="overview-retrieval-values">
          <span>召回 <b>{overview.rag_top_k}</b> 条</span>
          <span>最低分 <b>{overview.rag_min_score}</b></span>
          <span>相对阈值 <b>{overview.rag_relative_score}</b></span>
        </div>
        <div className="overview-protected-actions"><Button icon={<Settings2 size={15} />} disabled={!canManage} onClick={openRetrievalSettings}>调整参数</Button></div>
      </div>
    </section>

    <Modal
      title="调整检索参数"
      open={editingRetrieval}
      onCancel={() => setEditingRetrieval(false)}
      confirmLoading={savingRetrieval}
      onOk={() => void saveRetrievalSettings()}
      okText="保存并生效"
      cancelText="取消"
      width={460}
      footer={(_, { OkBtn, CancelBtn }) => <><Button danger type="text" loading={savingRetrieval} onClick={() => void resetRetrievalSettings()}>恢复 .env 默认值</Button><span className="retrieval-modal-spacer" /><CancelBtn /><OkBtn /></>}
    >
      <div className="retrieval-settings-form">
        <label><span>召回条数<small>最终返回给生成模型的知识条目数，建议 3–8。</small></span><InputNumber min={1} max={20} precision={0} value={retrievalDraft.top_k} onChange={value => setRetrievalDraft(current => ({ ...current, top_k: value ?? current.top_k }))} /></label>
        <label><span>最低分<small>过滤相关性过低的结果，范围 0–1。</small></span><InputNumber min={0} max={1} step={0.01} precision={2} value={retrievalDraft.min_score} onChange={value => setRetrievalDraft(current => ({ ...current, min_score: value ?? current.min_score }))} /></label>
        <label><span>相对阈值<small>相对最高分保留候选，范围 0–1。</small></span><InputNumber min={0} max={1} step={0.01} precision={2} value={retrievalDraft.relative_score} onChange={value => setRetrievalDraft(current => ({ ...current, relative_score: value ?? current.relative_score }))} /></label>
        {retrievalError && <p className="retrieval-settings-error">{retrievalError}</p>}
      </div>
    </Modal>

    <Tabs className="overview-main-tabs" defaultActiveKey="analytics" items={[
      { key: "analytics", label: "交互分析", children: <section className="overview-flat-section interaction-analysis-section">
        <Suspense fallback={<Spin />}><InteractionAnalyticsPanel token={token} /></Suspense>
      </section> },
      { key: "capacity", label: "并发与排队", children: <section className="overview-flat-section model-runtime-section"><Suspense fallback={<Spin />}><CapacityPanel token={token} embedded /></Suspense></section> },
      { key: "services", label: "服务配置", children: <section className="overview-flat-section model-runtime-section"><Suspense fallback={<Spin />}><ModelsPanel key={revision} token={token} embedded canManage={canManage} /></Suspense></section> },
    ]} />
  </>;
}

export function KnowledgeDashboard({ token, section, security, authorizationExpiry, onSecurityChanged }: { token: string; section: AdminSection; security: SecondarySecurityStatus | null; authorizationExpiry: string; onSecurityChanged: (status: SecondarySecurityStatus) => void }) {
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  const usesKnowledgeData = !["prompts", "announcements"].includes(section);
  const overview = useAdminData<Overview>(token, usesKnowledgeData ? "/overview" : null, revision);
  const releases = useAdminData<Release[]>(token, usesKnowledgeData ? "/releases" : null, revision);
  const release = releases.data?.find(r => r.id === selected)
    ?? releases.data?.find(r => r.status === "published") ?? releases.data?.[0];
  const canManage = security?.verified ?? false;
  function refresh(id?: string) { if (id) setSelected(id); setRevision(n => n + 1); }

  return <div className="knowledge-page">
    <section className="page-title-row">
      <div><h1>{sectionNames[section]}</h1><p>{subtitles[section]}</p></div>
      {usesKnowledgeData && <Button onClick={() => refresh()}>刷新</Button>}
    </section>
    <ErrorNotice error={overview.error ?? releases.error} />
    {usesKnowledgeData && !overview.data && !overview.error && <Spin />}
    {section === "overview" && overview.data && <OverviewContent token={token} revision={revision} overview={overview.data} releases={releases.data ?? []} onChanged={() => refresh()} security={security} authorizationExpiry={authorizationExpiry} onSecurityChanged={onSecurityChanged} />}
    {section === "tests" && <div className="release-picker">
      <span>操作版本</span><Select aria-label="操作版本" value={release?.id} placeholder="先创建知识版本"
        onChange={setSelected} options={releases.data?.map(r => ({ value: r.id, label: `${r.name} · ${labels[r.status]}` }))} />
      {release && <Tag color={release.status === "published" ? "green" : "blue"}>{labels[release.status]}</Tag>}
    </div>}
    <Suspense fallback={<Spin />}>
    {section === "knowledge" && <>
      <VersionsPanel token={token} releases={releases.data ?? []}
        release={release} selectedReleaseId={release?.id ?? ""} onSelectRelease={setSelected} onChanged={refresh} embeddingConfigured={overview.data?.embedding_configured ?? false}
        draftLimit={overview.data?.knowledge_draft_limit ?? 3} draftCount={overview.data?.knowledge_draft_count ?? 0} canManage={canManage} />
      {release ? <section className="knowledge-section knowledge-content-section">
        <div className="section-heading knowledge-content-heading"><div><h2>知识条目</h2><span>{release.entry_count.toLocaleString("zh-CN")} 条 · {release.status === "draft" ? "可编辑草稿" : "当前版本只读"}</span></div></div>
        <EntriesPanel key={`${release.id}-${revision}`} token={token} release={release} canManage={canManage} onChanged={refresh} />
      </section> : <Empty description="请先创建知识版本" />}
    </>}
    {section === "tests" && (release ? <RetrievalPanel key={release.id} token={token} release={release} overview={overview.data} canManage={canManage} /> : <Empty description="请先创建知识版本" />)}
    {section === "prompts" && <PromptsPanel token={token} canManage={canManage} />}
    {section === "announcements" && <AnnouncementsPanel token={token} canManage={canManage} />}
    </Suspense>
  </div>;
}
