import { Alert, Button, Checkbox, Empty, Input, InputNumber, Select, Table, Tag, Upload } from "antd";
import { useState } from "react";
import { knowledgeApi, type Operation, type Overview, type Release, type SearchResult } from "../../lib/api";
import { downloadJson, errorText } from "./shared";
import { ErrorNotice } from "./ErrorNotice";

interface EvalCase { query: string; expected_entry_ids: string[]; expect_no_results: boolean; query_source?: "alias" | "standard"; category?: string; standard_question?: string }
interface EvalRow extends EvalCase, SearchResult { passed: boolean; first_match_rank: number | null }
interface Report { metrics: Record<string, number | null>; cases: EvalRow[] }
interface GeneratedCases {
  release_id: string; cases: EvalCase[]; generated_count: number; alias_count: number;
  standard_count: number; available_entries: number; available_alias_entries: number; category_count: number;
}
const metricNames: Record<string, string> = { hit_rate: "命中率", recall_at_k: "Recall@K", mrr: "MRR", no_result_accuracy: "无结果准确率", pass_rate: "通过率", mean_latency_ms: "平均耗时 (ms)" };
const warnings: Record<string, string> = {
  embedding_model_changed_using_lexical: "模型已变更或全量重建尚未完成，本次使用关键词检索",
  embedding_request_failed_using_lexical: "向量服务失败，本次回退关键词检索",
  no_usable_embeddings_using_lexical: "没有可用向量或未配置服务，本次使用关键词检索",
  invalid_stored_vectors_using_lexical: "存在无效或维度不匹配的向量，相关条目使用关键词分数",
};
export function RetrievalPanel({ token, release, overview }: { token: string; release: Release; overview?: Overview }) {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [topK, setTopK] = useState(overview?.rag_top_k ?? 4);
  const [minScore, setMinScore] = useState(overview?.rag_min_score ?? 0.16);
  const [relative, setRelative] = useState(overview?.rag_relative_score ?? 0.62);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [expected, setExpected] = useState<string[]>([]);
  const [manualIds, setManualIds] = useState("");
  const [noResults, setNoResults] = useState(false);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [generateCount, setGenerateCount] = useState(50);
  const [generateSource, setGenerateSource] = useState("standard");
  const [generatedNotice, setGeneratedNotice] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [reportId, setReportId] = useState("");
  const options = { release_id: release.id, mode, top_k: topK, min_score: minScore, relative_score: relative };
  async function search() {
    setBusy(true); setError(""); setExpected([]); setResult(null);
    try { setResult(await knowledgeApi<SearchResult>(token, "/search", { method: "POST", body: JSON.stringify({ ...options, query }) })); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  function addCase() {
    const ids = [...new Set([...expected, ...manualIds.split(/[\s,，]+/).filter(Boolean)])];
    if (!query.trim() || (!noResults && !ids.length)) { setError("请输入测试问题，并勾选预期命中条目、填写 chunk ID，或选择预期无结果。"); return; }
    setCases(previous => [...previous, { query: query.trim(), expected_entry_ids: noResults ? [] : ids, expect_no_results: noResults }]);
    setError(""); setManualIds("");
  }
  async function evaluate() {
    setBusy(true); setError(""); setReport(null);
    try {
      const op = await knowledgeApi<Operation>(token, "/evaluations", { method: "POST", body: JSON.stringify({ ...options, cases }) });
      setReport(op.detail as unknown as Report); setReportId(op.id);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function generateCases() {
    setBusy(true); setError(""); setGeneratedNotice("");
    try {
      const generated = await knowledgeApi<GeneratedCases>(token, "/evaluation-cases/generate", {
        method: "POST",
        body: JSON.stringify({ release_id: release.id, limit: generateCount, source: generateSource }),
      });
      setCases(generated.cases);
      setGeneratedNotice(
        `已生成 ${generated.generated_count} 条，覆盖 ${generated.category_count} 个分类：其他问法 ${generated.alias_count} 条，标准问题 ${generated.standard_count} 条。`,
      );
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  return <>
    <Alert showIcon type="info" message="复用患者端检索逻辑；这里的参数仅用于本次测试。混合检索会调用 Embedding API，关键词模式可离线验证。这些指标衡量检索效果，不代表医疗回答正确率。" />
    <ErrorNotice error={error} />
    <section className="knowledge-section">
      <h2>单条检索测试</h2>
      <div className="toolbar test-parameters">
        <label>检索模式<Select aria-label="检索模式" value={mode} onChange={setMode} options={[{ value: "hybrid", label: "混合检索" }, { value: "lexical", label: "仅关键词" }]} /></label>
        <label>Top K<InputNumber aria-label="Top K" min={1} max={20} value={topK} onChange={v => setTopK(v ?? 4)} /></label>
        <label>最低分<InputNumber aria-label="最低分" min={0} max={1} step={0.01} value={minScore} onChange={v => setMinScore(v ?? 0.16)} /></label>
        <label>相对阈值<InputNumber aria-label="相对阈值" min={0} max={1} step={0.01} value={relative} onChange={v => setRelative(v ?? 0.62)} /></label>
      </div>
      <Input.TextArea aria-label="测试问题" placeholder="输入患者可能提出的问题，建议使用不同于标准问题的表达" value={query} onChange={e => setQuery(e.target.value)} autoSize={{ minRows: 2, maxRows: 6 }} maxLength={4000} />
      <div className="toolbar"><Button type="primary" loading={busy} disabled={!query.trim()} onClick={() => void search()}>测试检索</Button></div>
      {result && <>
        <p><Tag color="cyan">{result.mode === "hybrid" ? "实际：混合检索" : "实际：关键词检索"}</Tag>{result.hits.length} 条命中 · {result.latency_ms} ms</p>
        {result.warnings.map(w => <Alert key={w} type="warning" showIcon message={warnings[w] ?? w} />)}
        {!result.hits.length && <Empty description="没有命中，检查知识内容或调整阈值" />}
        {result.hits.map((hit, index) => <article className="hit-card" key={hit.entry_id}>
          <div className="section-heading"><h3>{index + 1}. {hit.question}</h3><Checkbox checked={expected.includes(hit.entry_id)} disabled={noResults} onChange={e => setExpected(previous => e.target.checked ? [...previous, hit.entry_id] : previous.filter(id => id !== hit.entry_id))}>预期命中</Checkbox></div>
          <p className="score-line">综合 {hit.score.toFixed(4)} · 关键词 {hit.lexical.toFixed(4)} · 语义 {hit.semantic?.toFixed(4) ?? "未使用"}</p>
          <p className="answer-preview">{hit.answer}</p><p className="muted">{hit.source_sheet} · 第 {hit.source_row} 行</p><div className="hit-chunk-id"><span>Chunk ID：</span><code>{hit.entry_id}</code></div>
        </article>)}
      </>}
    </section>
    <section className="knowledge-section">
      <h2>批量效果评测</h2>
      <p className="muted">可以先从当前知识库的标准问题生成基础评测集。系统按分类均衡抽样并自动绑定 Chunk ID；不会调用模型。</p>
      <div className="auto-eval-builder">
        <label>问题来源<Select aria-label="自动生成问题来源" value={generateSource} onChange={setGenerateSource} options={[
          { value: "mixed", label: "优先其他问法" }, { value: "aliases", label: "仅其他问法" }, { value: "standard", label: "仅标准问题" },
        ]} /></label>
        <label>抽样数量<Select aria-label="自动生成数量" value={generateCount} onChange={setGenerateCount} options={[20, 50, 100].map(value => ({ value, label: `${value} 条` }))} /></label>
        <Button type="primary" loading={busy} onClick={() => void generateCases()}>从知识库自动生成</Button>
      </div>
      {generatedNotice && <Alert type="success" showIcon message={generatedNotice} />}
      <p className="muted">自动集用于检查基础召回。为避免指标虚高，正式验收还应加入患者真实表达，以及知识库不应回答的“预期无结果”问题。</p>
      <p className="muted">下面仍可手工补充单条问题；若正确条目未被召回，可从分块详情复制其 ID。</p>
      <Input aria-label="预期 chunk ID" value={manualIds} onChange={e => setManualIds(e.target.value)} placeholder="可选：填写预期 chunk ID，多个用逗号分隔" disabled={noResults} />
      <div className="toolbar"><Checkbox checked={noResults} onChange={e => setNoResults(e.target.checked)}>预期无结果</Checkbox><Button disabled={cases.length >= 100} onClick={addCase}>加入评测集</Button></div>
      <Table<EvalCase> size="small" rowKey={(_, i) => String(i)} dataSource={cases} scroll={{ x: 500 }} pagination={{ pageSize: 5 }} columns={[
        { title: "测试问题", dataIndex: "query" },
        { title: "来源", render: (_, row) => row.expect_no_results ? "人工·无结果" : row.query_source === "alias" ? "其他问法" : row.query_source === "standard" ? "标准问题" : "人工" },
        { title: "标注", render: (_, row) => row.expect_no_results ? "无结果" : `${row.expected_entry_ids.length} 个 chunk` },
        { title: "操作", render: (_, row) => <Button size="small" onClick={() => setCases(previous => previous.filter(item => item !== row))}>移除</Button> },
      ]} />
      <div className="toolbar">
        <Button type="primary" disabled={!cases.length} loading={busy} onClick={() => void evaluate()}>运行评测（{cases.length}）</Button>
        <Button disabled={!cases.length} onClick={() => downloadJson({ release_id: release.id, cases }, "retrieval-cases.json")}>导出评测集</Button>
        <Upload accept=".json" showUploadList={false} beforeUpload={file => { void file.text().then(text => {
          const value = JSON.parse(text) as { release_id?: string; cases?: EvalCase[] };
          if (value.release_id && value.release_id !== release.id) throw new Error("评测集属于其他版本，请切换版本或重新标注 chunk ID。");
          if (!Array.isArray(value.cases) || value.cases.length > 100 || value.cases.some(c => typeof c.query !== "string" || !Array.isArray(c.expected_entry_ids) || c.expected_entry_ids.some(id => typeof id !== "string") || typeof c.expect_no_results !== "boolean")) throw new Error("评测集格式无效，最多 100 条。");
          setCases(value.cases); setError("");
        }).catch((e: unknown) => setError(errorText(e))); return false; }}><Button>导入评测集</Button></Upload>
      </div>
      {report && <>
        <div className="eval-metrics">{Object.entries(report.metrics).map(([key, value]) => <div key={key}><span>{metricNames[key] ?? key}</span><strong>{value === null ? "不适用" : key === "mean_latency_ms" ? value.toFixed(1) : value.toFixed(3)}</strong></div>)}</div>
        <Table<EvalRow> rowKey={(_, i) => String(i)} dataSource={report.cases} size="small" scroll={{ x: 550 }} columns={[
          { title: "问题", dataIndex: "query" }, { title: "结果", render: (_, r) => <Tag color={r.passed ? "green" : "red"}>{r.passed ? "通过" : "未通过"}</Tag> },
          { title: "首个正确排名", dataIndex: "first_match_rank", render: (v: number | null) => v ?? "—" },
          { title: "模式", dataIndex: "mode" },
        ]} expandable={{ expandedRowRender: r => <pre className="content-preview">{JSON.stringify(r, null, 2)}</pre> }} />
        <Button onClick={() => downloadJson({ id: reportId, ...report }, `retrieval-report-${reportId}.json`)}>导出评测报告</Button>
      </>}
    </section>
  </>;
}
