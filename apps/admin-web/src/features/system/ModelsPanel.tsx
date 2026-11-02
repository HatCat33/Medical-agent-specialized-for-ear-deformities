import { Alert, Button, Drawer, Form, Grid, Input, InputNumber, Popconfirm, Select, Space, Spin, Switch, Table, Tabs, Tag, Tooltip } from "antd";
import { Activity, RotateCcw, Settings2 } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";

import { ApiError, type ModelConfig, type ModelConfigurationInventory, type ModelInventory, type ModelProbe, systemApi } from "../../lib/api";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText } from "../knowledge/shared";

const SpeechPanel = lazy(() => import("./SpeechPanel").then(module => ({ default: module.SpeechPanel })));

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
  reranker_request_failed: "Rerank 请求失败", reranker_response_invalid: "Rerank 返回格式无效",
  probe_failed: "探活失败",
};

export function ModelsPanel({ token }: { token: string }) {
  const screens = Grid.useBreakpoint();
  const compact = !screens.sm;
  const [inventory, setInventory] = useState<ModelInventory | null>(null);
  const [configuration, setConfiguration] = useState<ModelConfigurationInventory | null>(null);
  const [selected, setSelected] = useState<ModelConfig | null>(null);
  const [configTab, setConfigTab] = useState("model");
  const [form] = Form.useForm();
  const selectedProtocol = Form.useWatch("protocol", form);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [configurationError, setConfigurationError] = useState("");
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
    void Promise.allSettled([
      systemApi<ModelInventory>(token, "/models"),
      systemApi<ModelConfigurationInventory>(token, "/models/configuration"),
    ])
      .then(([inventoryResult, configurationResult]) => { if (active) {
        if (inventoryResult.status === "fulfilled") {
          const data = inventoryResult.value;
          setInventory(data); setError("");
          setExpanded(data.items.filter(item => item.last_probe && ["unhealthy", "not_configured"].includes(item.last_probe.status)).map(item => item.kind));
        } else {
          setError(errorText(inventoryResult.reason));
        }
        if (configurationResult.status === "fulfilled") {
          setConfiguration(configurationResult.value); setConfigurationError("");
        } else {
          setConfiguration(null);
          setConfigurationError(configurationResult.reason instanceof ApiError && configurationResult.reason.status === 404
            ? "当前后端进程尚未加载模型配置接口。请重启后端，刷新页面后即可配置；模型探活仍可继续使用。"
            : errorText(configurationResult.reason));
        }
      } });
    return () => { active = false; };
  }, [token, revision]);

  function edit(row: ModelConfig) {
    setConfigTab("model");
    const item = configuration?.items.find(value => value.kind === row.kind);
    form.resetFields();
    form.setFieldsValue({ ...(item?.values ?? {}), api_key: "" });
    setSelected(row);
  }

  function selectTtsProtocol(protocol: string) {
    const current = form.getFieldsValue(["base_url", "model"]);
    const patch: Record<string, unknown> = { protocol };
    if (protocol === "azure-speech") {
      if (!String(current.base_url ?? "").includes(".tts.speech.microsoft.com")) patch.base_url = "";
      patch.model = "Azure Neural TTS";
      patch.streaming_enabled = true;
    } else if (protocol === "elevenlabs") {
      if (!String(current.base_url ?? "").includes("elevenlabs.io")) patch.base_url = "https://api.elevenlabs.io";
      if (current.model !== "eleven_flash_v2_5") patch.model = "eleven_flash_v2_5";
      patch.streaming_enabled = true;
    } else if (protocol === "volcengine") {
      if (!String(current.base_url ?? "").includes("openspeech.bytedance.com")) patch.base_url = "https://openspeech.bytedance.com";
      if (!String(current.model ?? "").startsWith("seed-")) patch.model = "seed-tts-2.0";
      patch.streaming_enabled = true;
    }
    form.setFieldsValue(patch);
  }

  async function saveConfiguration() {
    if (!selected) return;
    const values = await form.validateFields() as Record<string, unknown>;
    if (!values.api_key) delete values.api_key;
    setBusy(`save:${selected.kind}`); setError("");
    try {
      await systemApi(token, `/models/${selected.kind}/configuration`, { method: "PUT", body: JSON.stringify(values) });
      if (selected.kind === "tts") setConfigTab("speech");
      else setSelected(null);
      setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(null); }
  }

  async function resetConfiguration() {
    if (!selected) return;
    setBusy(`save:${selected.kind}`); setError("");
    try {
      await systemApi(token, `/models/${selected.kind}/configuration`, { method: "DELETE" });
      setSelected(null); setRevision(value => value + 1);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(null); }
  }

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
        {configurationError && <Alert className="model-configuration-notice" type="warning" showIcon message="模型配置暂不可用" description={configurationError} />}
        <Table<ModelConfig> rowKey="kind" size="small" pagination={false} tableLayout="fixed" dataSource={inventory.items} columns={[
          { title: "能力", dataIndex: "label", width: compact ? 70 : 110, className: "model-kind-column" },
          { title: compact ? "模型" : "Provider / 模型", width: compact ? 150 : 230, render: (_, row) => <div className="model-identity"><strong>{row.provider || "—"}</strong><span>{row.model || "未填写模型名"}</span>{row.active !== undefined && <Tag color={row.active ? "green" : "default"}>{row.active ? "患者端当前使用" : "备用播报方式"}</Tag>}{row.kind === "tts" && <span>音色：{String(row.settings.voice || "未配置")}</span>}{row.client_only && <span>{speechProbe || "设备能力 · 点击探活试听"}</span>}</div> },
          { title: "服务地址", dataIndex: "endpoint_host", responsive: ["lg"], render: (value: string) => value || "—" },
          { title: "配置", width: 110, responsive: ["md"], render: (_, row) => <Tag color={row.client_only ? "default" : row.configured ? "green" : "default"}>{row.client_only ? "需本机验证" : row.configured ? "已配置" : "不完整"}</Tag> },
          { title: "最近探活", width: 190, responsive: ["sm"], render: (_, row) => row.last_probe ? <div className="probe-result"><Tag color={statusColor[row.last_probe.status]}>{statusText[row.last_probe.status] ?? row.last_probe.status}</Tag><span>{row.last_probe.latency_ms?.toFixed(0) ?? "—"} ms · {new Date(row.last_probe.created_at).toLocaleString("zh-CN", { hour12: false })}</span>{row.last_probe.error && <Tooltip title={probeErrors[row.last_probe.error] ?? row.last_probe.error}><code>{probeErrors[row.last_probe.error] ?? row.last_probe.error}</code></Tooltip>}</div> : "尚未探活" },
          { title: "操作", width: compact ? 112 : 150, render: (_, row) => <Space size={6}><Button size="small" title={row.client_only ? "测试当前管理员设备播报，不代表患者设备" : "发送测试请求，可能产生调用费用"} disabled={busy !== null && busy !== row.kind} loading={busy === row.kind} onClick={() => void probe(row.kind)}>探活</Button>{!row.client_only && <Tooltip title={!configuration ? "重启后端后可配置" : "修改模型配置"}><span><Button size="small" icon={<Settings2 size={14} />} disabled={!configuration} onClick={() => edit(row)} aria-label={`配置${row.label}`}>{compact ? null : "配置"}</Button></span></Tooltip>}</Space> },
        ]} expandable={{ expandedRowKeys: expanded, onExpandedRowsChange: keys => setExpanded([...keys]), expandedRowRender: row => <div className="model-expanded">
          {diagnostics(row)}
          {row.kind === "tts" && row.last_probe && <p className="muted">上次探活使用：{String(row.last_probe.detail.model ?? "未知模型")} · {String(row.last_probe.detail.voice ?? "未知音色")} · {String(row.last_probe.detail.configuration ?? "已保存设置")}{row.last_probe.detail.first_audio_ms != null ? ` · 首包 ${Number(row.last_probe.detail.first_audio_ms).toFixed(0)} ms` : ""}</p>}
          <dl className="model-settings"><div><dt>配置状态</dt><dd>{row.configured ? "已配置" : "不完整"}</dd></div><div><dt>服务地址</dt><dd>{row.endpoint_host || "—"}</dd></div>{Object.entries(row.settings).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value === null ? "默认" : String(value)}</dd></div>)}</dl><div className="model-expanded-probe">最近探活：{row.last_probe ? `${statusText[row.last_probe.status] ?? row.last_probe.status} · ${row.last_probe.latency_ms?.toFixed(0) ?? "—"} ms · ${new Date(row.last_probe.created_at).toLocaleString("zh-CN", { hour12: false })}` : "尚未探活"}</div></div> }} />
      </section>
    </>}
    <Drawer title={selected ? `配置${selected.label}` : "模型配置"} open={!!selected} onClose={() => setSelected(null)} width={selected?.kind === "tts" ? 640 : 520} destroyOnClose={false} extra={configTab === "model" && <Space><Popconfirm title="恢复 .env 配置？" description="将删除该能力在管理端保存的覆盖值。" okText="恢复" cancelText="取消" onConfirm={() => void resetConfiguration()}><Button danger type="text" icon={<RotateCcw size={15} />} disabled={!selected || !configuration?.overridden_kinds.includes(selected.kind)}>恢复默认</Button></Popconfirm><Button type="primary" loading={busy === `save:${selected?.kind}`} onClick={() => void saveConfiguration()}>保存并生效</Button></Space>}>
      {selected?.kind === "tts" && <Tabs activeKey={configTab} onChange={setConfigTab} items={[{ key: "model", label: "模型连接" }, { key: "speech", label: "音色与播报" }]} />}
      {selected?.kind === "tts" && configTab === "speech" && <Suspense fallback={<Spin />}><SpeechPanel token={token} onSaved={() => setRevision(value => value + 1)} /></Suspense>}
      {selected && <Form hidden={configTab !== "model"} form={form} layout="vertical" className="model-config-form" requiredMark={false}>
        <Alert type="info" showIcon message="保存后当前后端进程立即生效" description="API Key 仅写入，不会在页面或接口中返回；留空会保留当前密钥。" />
        {(selected.kind === "chat" || selected.kind === "asr") && <Form.Item name="provider" label="服务类型"><Select options={(selected.kind === "chat" ? ["openai-compatible", "demo"] : ["openai-compatible", "disabled"]).map(value => ({ value, label: value }))} /></Form.Item>}
        {selected.kind === "chat_fallback" && <Alert type="info" showIcon message="主模型重试后自动降级" description="仅当主模型在输出首个片段前连续失败时调用此接口，不会把两家模型的半截回答拼在一起。" />}
        {selected.kind === "reranker" && <Form.Item name="enabled" label="启用 Rerank" valuePropName="checked"><Switch checkedChildren="开启" unCheckedChildren="关闭" /></Form.Item>}
        {selected.kind === "tts" && <Form.Item name="protocol" label="接口协议"><Select onChange={selectTtsProtocol} options={[
          { value: "openai-compatible", label: "OpenAI 兼容接口" },
          { value: "azure-speech", label: "Azure Speech" },
          { value: "elevenlabs", label: "ElevenLabs" },
          { value: "volcengine", label: "火山引擎豆包语音" },
          { value: "custom", label: "自定义 JSON 接口" },
          { value: "local-sherpa", label: "本地中文语音（CPU，无需 API Key）" },
        ]} /></Form.Item>}
        {selected.kind === "tts" && selectedProtocol === "azure-speech" && <Alert type="info" showIcon message="Azure 低延迟中文语音" description="服务地址填写资源所在区域，例如 https://eastasia.tts.speech.microsoft.com；API Key 填写该 Speech 资源的 Key。保存后到“音色与播报”选择晓晓等中文音色。" />}
        {selected.kind === "tts" && selectedProtocol === "elevenlabs" && <Alert type="info" showIcon message="ElevenLabs Flash" description="已自动选择 eleven_flash_v2_5 和流式音频；API Key 与音色 ID 需要从 ElevenLabs 控制台获取。" />}
        {selected.kind === "tts" && selectedProtocol === "volcengine" && <Alert type="info" showIcon message="火山引擎豆包语音 V3" description="使用新版控制台的 API Key。模型名称填写资源 ID（默认 seed-tts-2.0）；保存后到“音色与播报”填写已开通的 Speaker ID。音频通过国内 SSE 接口边合成边播放。" />}
        <Form.Item name="base_url" label="服务地址" rules={[{ type: "url", warningOnly: true, message: "建议填写完整的 http(s) 地址" }]}><Input placeholder={selected.kind === "tts" && selectedProtocol === "azure-speech" ? "https://<区域>.tts.speech.microsoft.com" : "https://api.example.com/v1"} /></Form.Item>
        <Form.Item name="api_key" label="API Key" extra={configuration?.items.find(item => item.kind === selected.kind)?.api_key_configured ? "已配置密钥；留空则保持不变" : "尚未配置密钥"}><Input.Password autoComplete="new-password" placeholder="留空不修改" /></Form.Item>
        <Form.Item name="model" label="模型名称"><Input disabled={selected.kind === "tts" && selectedProtocol === "azure-speech"} placeholder="填写供应商模型 ID" /></Form.Item>
        {selected.kind === "tts" && <><p className="muted">先保存模型连接，再到“音色与播报”选择音色、调整语速和试听。</p><Form.Item name="streaming_enabled" label="流式音频" valuePropName="checked" extra="仅在供应商确实返回原始 PCM 字节流时开启"><Switch checkedChildren="开启" unCheckedChildren="关闭" /></Form.Item></>}
        {selected.kind === "asr" && <Form.Item name="language" label="识别语言"><Input placeholder="zh" /></Form.Item>}
        <Form.Item name="timeout_seconds" label="超时时间（秒）"><InputNumber min={1} max={300} precision={0} style={{ width: "100%" }} /></Form.Item>
        {["chat", "chat_fallback", "vision"].includes(selected.kind) && <><Form.Item name="max_tokens" label="最大输出 Token"><InputNumber min={1} max={100000} precision={0} style={{ width: "100%" }} /></Form.Item><Form.Item name="enable_thinking" label="思考模式" valuePropName="checked"><Switch checkedChildren="开启" unCheckedChildren="关闭" /></Form.Item></>}
        {selected.kind === "chat" && <Form.Item name="retry_count" label="首包前重试次数" extra="推荐 1 次；仍失败后自动请求备用对话模型"><InputNumber min={0} max={3} precision={0} style={{ width: "100%" }} /></Form.Item>}
        {selected.kind === "embedding" && <Form.Item name="batch_size" label="向量批大小"><InputNumber min={1} max={256} precision={0} style={{ width: "100%" }} /></Form.Item>}
        {selected.kind === "reranker" && <Form.Item name="candidate_k" label="候选条数" extra="先由混合检索召回，再从这些候选中精排 Top K"><InputNumber min={1} max={100} precision={0} style={{ width: "100%" }} /></Form.Item>}
      </Form>}
    </Drawer>
  </>;
}
