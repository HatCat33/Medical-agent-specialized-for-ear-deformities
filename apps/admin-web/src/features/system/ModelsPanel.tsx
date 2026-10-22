import { Alert, Button, Grid, Spin, Table, Tag, Tooltip } from "antd";
import { Activity } from "lucide-react";
import { useEffect, useState } from "react";

import { type ModelConfig, type ModelInventory, type ModelProbe, systemApi } from "../../lib/api";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText } from "../knowledge/shared";

const statusText: Record<string, string> = {
  healthy: "正常", unhealthy: "异常", not_configured: "未配置", client_only: "客户端能力",
};
const statusColor: Record<string, string> = {
  healthy: "green", unhealthy: "red", not_configured: "default", client_only: "blue",
};
const probeErrors: Record<string, string> = {
  model_not_configured: "配置不完整", model_request_failed: "请求失败", model_response_empty: "模型返回空内容",
  model_response_invalid: "返回格式无效", embedding_request_failed: "请求失败", embedding_response_invalid: "向量无效",
  embedding_count_mismatch: "向量数量不一致", asr_response_invalid: "语音服务返回格式无效", probe_timeout: "探活超时",
  probe_failed: "探活失败",
};

export function ModelsPanel({ token }: { token: string }) {
  const screens = Grid.useBreakpoint();
  const compact = !screens.sm;
  const [inventory, setInventory] = useState<ModelInventory | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [speechProbe, setSpeechProbe] = useState("");
  const [expanded, setExpanded] = useState<React.Key[]>([]);

  function diagnostics(row: ModelConfig) {
    const probe = row.last_probe;
    if (!probe || !["unhealthy", "not_configured"].includes(probe.status)) return null;
    const detail = probe.detail;
    return <Alert className="probe-diagnostics" type="error" showIcon message={String(detail.summary ?? probeErrors[probe.error] ?? probe.error)} description={<div>
      <p>{String(detail.suggestion ?? "重新探活以获取详细诊断信息。")}</p>
      {detail.http_status != null && <p>HTTP 状态：<code>{String(detail.http_status)}</code></p>}
      {!!detail.upstream_message && <p>供应商说明：{String(detail.upstream_message)}</p>}
      {!!detail.upstream_code && <p>供应商错误码：<code>{String(detail.upstream_code)}</code></p>}
      {!!detail.request_id && <p>供应商请求编号：<code>{String(detail.request_id)}</code></p>}
      <p>探活记录：<code>{probe.id}</code> · {probe.error}</p>
    </div>} />;
  }

  async function probeBrowserSpeech() {
    setSpeechProbe("");
    if (!("speechSynthesis" in window)) { setSpeechProbe("当前设备不支持"); return; }
    const synth = window.speechSynthesis;
    synth.cancel();
    await new Promise<void>(resolve => {
      const utterance = new SpeechSynthesisUtterance("语音播报测试。");
      utterance.lang = "zh-CN";
      const timer = window.setTimeout(() => { setSpeechProbe("设备播报超时"); synth.cancel(); resolve(); }, 8000);
      const done = (status: string) => { window.clearTimeout(timer); setSpeechProbe(status); resolve(); };
      utterance.onend = () => done("本机播报正常");
      utterance.onerror = () => done("本机播报不可用");
      synth.speak(utterance);
    });
  }

  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  useEffect(() => {
    let active = true;
    void systemApi<ModelInventory>(token, "/models")
      .then(data => { if (active) {
        setInventory(data); setError("");
        setExpanded(data.items.filter(item => item.last_probe && ["unhealthy", "not_configured"].includes(item.last_probe.status)).map(item => item.kind));
      } })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); });
    return () => { active = false; };
  }, [token, revision]);

  async function probe(kind: string) {
    setBusy(kind); setError("");
    try {
      if (inventory?.items.find(item => item.kind === kind)?.client_only) await probeBrowserSpeech();
      const result = await systemApi<ModelProbe>(token, `/models/${kind}/probe`, { method: "POST" });
      if (["unhealthy", "not_configured"].includes(result.status)) setExpanded(values => [...new Set([...values, kind])]);
      setRevision(value => value + 1);
    }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(null); }
  }

  async function probeAll() {
    setBusy("all"); setError("");
    try {
      const browserProbe = inventory?.items.some(item => item.client_only) ? probeBrowserSpeech() : Promise.resolve();
      const result = await systemApi<{ items: ModelProbe[] }>(token, "/models/probe-all", { method: "POST" });
      setExpanded(result.items.filter(item => ["unhealthy", "not_configured"].includes(item.status)).map(item => item.target_id));
      await browserProbe; setRevision(value => value + 1);
    }
    catch (reason) { setError(errorText(reason)); }
    finally { setBusy(null); }
  }

  if (!inventory && !error) return <Spin />;
  return <>
    <ErrorNotice error={error} />
    {inventory && <>
      <section className="knowledge-section">
        <div className="section-heading">
          <div><h2>模型服务</h2><span>当前配置与最近探活状态</span></div>
          <Tooltip title="测试请求可能产生少量模型调用费用"><Button icon={<Activity size={16} />} loading={busy === "all"} disabled={busy !== null && busy !== "all"} onClick={() => void probeAll()}>全部探活</Button></Tooltip>
        </div>
        <Table<ModelConfig> rowKey="kind" size="small" pagination={false} tableLayout="fixed" dataSource={inventory.items} columns={[
          { title: "能力", dataIndex: "label", width: compact ? 70 : 110, className: "model-kind-column" },
          { title: compact ? "模型" : "Provider / 模型", width: compact ? 150 : 230, render: (_, row) => <div className="model-identity"><strong>{row.provider || "—"}</strong><span>{row.model || "未填写模型名"}</span>{row.active !== undefined && <Tag color={row.active ? "green" : "default"}>{row.active ? "患者端当前使用" : "备用播报方式"}</Tag>}{row.kind === "tts" && <span>音色：{String(row.settings.voice || "未配置")}</span>}{row.client_only && <span>{speechProbe || "设备能力 · 点击探活试听"}</span>}</div> },
          { title: "服务地址", dataIndex: "endpoint_host", responsive: ["lg"], render: (value: string) => value || "—" },
          { title: "配置", width: 110, responsive: ["md"], render: (_, row) => <Tag color={row.client_only ? "default" : row.configured ? "green" : "default"}>{row.client_only ? "需本机验证" : row.configured ? "已配置" : "不完整"}</Tag> },
          { title: "最近探活", width: 190, responsive: ["sm"], render: (_, row) => row.last_probe ? <div className="probe-result"><Tag color={statusColor[row.last_probe.status]}>{statusText[row.last_probe.status] ?? row.last_probe.status}</Tag><span>{row.last_probe.latency_ms?.toFixed(0) ?? "—"} ms · {new Date(row.last_probe.created_at).toLocaleString("zh-CN", { hour12: false })}</span>{row.last_probe.error && <Tooltip title={probeErrors[row.last_probe.error] ?? row.last_probe.error}><code>{probeErrors[row.last_probe.error] ?? row.last_probe.error}</code></Tooltip>}</div> : "尚未探活" },
          { title: "操作", width: compact ? 65 : 80, render: (_, row) => <Button size="small" title={row.client_only ? "测试当前管理员设备播报，不代表患者设备" : "发送测试请求，可能产生调用费用"} disabled={busy !== null && busy !== row.kind} loading={busy === row.kind} onClick={() => void probe(row.kind)}>探活</Button> },
        ]} expandable={{ expandedRowKeys: expanded, onExpandedRowsChange: keys => setExpanded([...keys]), expandedRowRender: row => <div className="model-expanded">
          {diagnostics(row)}
          {row.kind === "tts" && row.last_probe && <p className="muted">上次探活使用：{String(row.last_probe.detail.model ?? "未知模型")} · {String(row.last_probe.detail.voice ?? "未知音色")} · {String(row.last_probe.detail.configuration ?? "已保存设置")}</p>}
          <dl className="model-settings"><div><dt>配置状态</dt><dd>{row.configured ? "已配置" : "不完整"}</dd></div><div><dt>服务地址</dt><dd>{row.endpoint_host || "—"}</dd></div>{Object.entries(row.settings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value === null ? "默认" : String(value)}</dd></div>)}</dl><div className="model-expanded-probe">最近探活：{row.last_probe ? `${statusText[row.last_probe.status] ?? row.last_probe.status} · ${row.last_probe.latency_ms?.toFixed(0) ?? "—"} ms · ${new Date(row.last_probe.created_at).toLocaleString("zh-CN", { hour12: false })}` : "尚未探活"}</div></div> }} />
      </section>
    </>}
  </>;
}
