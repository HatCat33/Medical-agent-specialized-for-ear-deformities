import { Button, Empty, Select, Spin, Tag } from "antd";
import { lazy, Suspense, useState } from "react";
import { type Overview, type Release } from "../../lib/api";
import { labels, useAdminData } from "./shared";
import { ErrorNotice } from "./ErrorNotice";
import { type AdminSection, sectionNames } from "./navigation";

const EntriesPanel = lazy(() => import("./EntriesPanel").then(m => ({ default: m.EntriesPanel })));
const OperationsPanel = lazy(() => import("./OperationsPanel").then(m => ({ default: m.OperationsPanel })));
const RetrievalPanel = lazy(() => import("./RetrievalPanel").then(m => ({ default: m.RetrievalPanel })));
const VersionsPanel = lazy(() => import("./VersionsPanel").then(m => ({ default: m.VersionsPanel })));
const PromptsPanel = lazy(() => import("../system/PromptsPanel").then(m => ({ default: m.PromptsPanel })));
const ModelsPanel = lazy(() => import("../system/ModelsPanel").then(m => ({ default: m.ModelsPanel })));
const SpeechPanel = lazy(() => import("../system/SpeechPanel").then(m => ({ default: m.SpeechPanel })));

const subtitles: Record<AdminSection, string> = {
  overview: "服务状态与当前检索配置",
  versions: "医院资料版本发布与回退",
  chunks: "知识内容查看、编辑与删除",
  tests: "单条检索验证与批量效果评测",
  prompts: "系统与业务提示词的版本发布",
  speech: "患者端默认播报与音色参数",
  operations: "后台任务、评测报告与审计记录",
};

export function KnowledgeDashboard({ token, section }: { token: string; section: AdminSection }) {
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  const usesKnowledgeData = !["prompts", "speech"].includes(section);
  const overview = useAdminData<Overview>(token, usesKnowledgeData ? "/overview" : null, revision);
  const releases = useAdminData<Release[]>(token, usesKnowledgeData ? "/releases" : null, revision);
  const release = releases.data?.find(r => r.id === selected)
    ?? releases.data?.find(r => r.status === "published") ?? releases.data?.[0];
  function refresh(id?: string) { if (id) setSelected(id); setRevision(n => n + 1); }

  return <div className="knowledge-page">
    <section className="page-title-row">
      <div><h1>{sectionNames[section]}</h1><p>{subtitles[section]}</p></div>
      {usesKnowledgeData && <Button onClick={() => refresh()}>刷新</Button>}
    </section>
    <ErrorNotice error={overview.error ?? releases.error} />
    {usesKnowledgeData && !overview.data && !overview.error && <Spin />}
    {section === "overview" && overview.data && <>
      <div className="metric-strip">
        <div className="metric-item"><span>患者端 RAG</span><strong>{overview.data.rag_enabled ? "已开启" : "未开启"}</strong></div>
        <div className="metric-item"><span>当前发布条目</span><strong>{releases.data?.find(r => r.status === "published")?.entry_count ?? 0}</strong></div>
        <div className="metric-item"><span>后台任务</span><strong>{overview.data.active_jobs}</strong></div>
      </div>
      <Suspense fallback={<Spin />}><ModelsPanel key={revision} token={token} /></Suspense>
      <section className="knowledge-section">
        <h2>当前检索配置</h2>
        <dl className="config-grid">
          <div><dt>数据库</dt><dd>{overview.data.database}</dd></div>
          <div><dt>分块方式</dt><dd>{overview.data.chunk_strategy}</dd></div>
          <div><dt>向量存储</dt><dd>{overview.data.vector_storage}</dd></div>
          <div><dt>检索参数</dt><dd>Top K {overview.data.rag_top_k} · 最低分 {overview.data.rag_min_score} · 相对阈值 {overview.data.rag_relative_score}</dd></div>
        </dl>
      </section>
    </>}
    {["versions", "chunks", "tests"].includes(section) && <div className="release-picker">
      <span>操作版本</span><Select aria-label="操作版本" value={release?.id} placeholder="先创建知识版本"
        onChange={setSelected} options={releases.data?.map(r => ({ value: r.id, label: `${r.name} · ${labels[r.status]}` }))} />
      {release && <Tag color={release.status === "published" ? "green" : "blue"}>{labels[release.status]}</Tag>}
    </div>}
    <Suspense fallback={<Spin />}>
    {section === "versions" && <VersionsPanel token={token} releases={releases.data ?? []}
      release={release} onChanged={refresh} embeddingConfigured={overview.data?.embedding_configured ?? false} />}
    {section === "chunks" && (release ? <EntriesPanel key={`${release.id}-${revision}`} token={token} release={release} onChanged={refresh} /> : <Empty description="请先创建知识版本" />)}
    {section === "tests" && (release ? <RetrievalPanel key={release.id} token={token} release={release} overview={overview.data} /> : <Empty description="请先创建知识版本" />)}
    {section === "prompts" && <PromptsPanel token={token} />}
    {section === "speech" && <SpeechPanel token={token} />}
    {section === "operations" && <OperationsPanel token={token} revision={revision} />}
    </Suspense>
  </div>;
}
