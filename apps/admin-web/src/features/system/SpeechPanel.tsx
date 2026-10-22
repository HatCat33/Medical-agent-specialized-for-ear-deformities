import { Alert, Button, Input, Select, Slider, Spin, Switch } from "antd";
import { Activity, Save, Square, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type ModelProbe, type SpeechProviderInfo, type SpeechSettings, SpeechPreviewError, speechPreview, systemApi } from "../../lib/api";
import { SpeechDiagnostics } from "./SpeechDiagnostics";
import { ErrorNotice } from "../knowledge/ErrorNotice";
import { errorText } from "../knowledge/shared";

export function SpeechPanel({ token }: { token: string }) {
  const [settings, setSettings] = useState<SpeechSettings | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewStatus, setPreviewStatus] = useState("");
  const [provider, setProvider] = useState<SpeechProviderInfo | null>(null);
  const [diagnostics, setDiagnostics] = useState<Record<string, unknown> | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState("");
  const [voiceFailures, setVoiceFailures] = useState<Record<string, string>>({});
  const [showOnline, setShowOnline] = useState(false);
  const [fallbackNotice, setFallbackNotice] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const activeUtterance = useRef<SpeechSynthesisUtterance | null>(null);
  const browserTimer = useRef<number | null>(null);
  const previewGeneration = useRef(0);
  const previewRequest = useRef<AbortController | null>(null);
  const previewUrl = useRef<string | null>(null);
  const [preview, setPreview] = useState("你好，我是耳畸形科普助手。请问有什么可以帮助你？");

  useEffect(() => {
    let active = true;
    void systemApi<SpeechSettings>(token, "/speech")
      .then(data => { if (active) setSettings(data); })
      .catch((reason: unknown) => { if (active) setError(errorText(reason)); });
    void systemApi<typeof provider>(token, "/speech/provider").then(value => { if (active) setProvider(value); }).catch(() => undefined);
    const synth = window.speechSynthesis;
    const update = () => setVoices(synth?.getVoices() ?? []);
    update();
    synth?.addEventListener("voiceschanged", update);
    return () => {
      previewGeneration.current += 1;
      if (browserTimer.current !== null) window.clearTimeout(browserTimer.current);
      browserTimer.current = null; activeUtterance.current = null;
      active = false; synth?.removeEventListener("voiceschanged", update); synth?.cancel();
      previewRequest.current?.abort(); audioRef.current?.pause();
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    };
  }, [token]);

  function change(patch: Partial<SpeechSettings>) {
    stopPreview();
    setDiagnostics(null); setProbeResult(""); setFallbackNotice(""); setError("");
    setSettings(value => value ? { ...value, ...patch } : value); setSaved(false);
  }

  async function probeCloud() {
    if (!settings) return;
    setProbing(true); setDiagnostics(null); setProbeResult(""); setError("");
    try {
      const result = await systemApi<ModelProbe>(token, "/speech/probe", { method: "POST", body: JSON.stringify(settings) });
      if (result.status === "healthy") setProbeResult(`合成成功 · ${result.latency_ms?.toFixed(0)} ms · ${String(result.detail.voice)}`);
      else setDiagnostics(result.detail);
    } catch (reason) { setError(errorText(reason)); }
    finally { setProbing(false); }
  }

  async function save() {
    setBusy(true); setError(""); setSaved(false);
    try {
      await systemApi(token, "/speech", { method: "PUT", body: JSON.stringify(settings) });
      setSaved(true);
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  }

  function stopPreview() {
    previewGeneration.current += 1;
    if (browserTimer.current !== null) window.clearTimeout(browserTimer.current);
    browserTimer.current = null; activeUtterance.current = null;
    previewRequest.current?.abort(); previewRequest.current = null;
    audioRef.current?.pause(); audioRef.current = null;
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    window.speechSynthesis?.cancel(); setPreviewing(false); setPreviewStatus("");
  }

  function speakBrowserPreview(generation: number, useConfiguredVoice: boolean) {
    const synth = window.speechSynthesis;
    if (!settings || !synth || generation !== previewGeneration.current) return;
    const available = synth.getVoices();
    if (available.length && available !== voices) setVoices(available);
    const configured = available.find(voice => settings.voice && (voice.voiceURI === settings.voice || voice.name === settings.voice));
    const language = settings.language.toLowerCase();
    const baseLanguage = language.split("-")[0];
    const sameLanguage = available.filter(voice => voice.lang.toLowerCase() === language);
    const sameBaseLanguage = available.filter(voice => voice.lang.split("-")[0].toLowerCase() === baseLanguage);
    const fallback = sameLanguage.find(voice => voice.localService && voice !== configured)
      ?? sameBaseLanguage.find(voice => voice.localService && voice !== configured)
      ?? sameLanguage[0] ?? sameBaseLanguage[0] ?? available.find(voice => voice.default) ?? null;
    const selected = useConfiguredVoice ? configured ?? fallback : fallback;
    const canRetryLocally = useConfiguredVoice && configured != null && fallback != null && fallback.localService && configured !== fallback;
    const utterance = new SpeechSynthesisUtterance(preview);
    utterance.voice = selected; utterance.lang = selected?.lang ?? settings.language;
    utterance.rate = settings.rate; utterance.pitch = settings.pitch; utterance.volume = settings.volume;
    const retryOrFail = (reason = "启动超时") => {
      if (generation !== previewGeneration.current || activeUtterance.current !== utterance) return;
      if (browserTimer.current !== null) window.clearTimeout(browserTimer.current);
      browserTimer.current = null; activeUtterance.current = null;
      if (selected) setVoiceFailures(current => ({ ...current, [selected.voiceURI]: reason }));
      synth.cancel();
      if (canRetryLocally) {
        setFallbackNotice(`所选音色 ${configured.name} 未能播放（${reason}），本次改用 ${fallback.name}。保存的音色尚未更改。`);
        setPreviewStatus("正在切换本机音色…");
        window.setTimeout(() => speakBrowserPreview(generation, false), 80);
      } else {
        setPreviewing(false); setPreviewStatus("");
        setError(`设备音色不可用（${reason}）。请选择其他本机音色；没有可用音色时可使用云端语音。`);
      }
    };
    utterance.onstart = () => {
      if (generation !== previewGeneration.current || activeUtterance.current !== utterance) return;
      if (browserTimer.current !== null) window.clearTimeout(browserTimer.current);
      browserTimer.current = null;
      if (selected) setVoiceFailures(current => { const next = { ...current }; delete next[selected.voiceURI]; return next; });
      setPreviewStatus(`正在播放 · ${selected?.name ?? "系统默认音色"}`);
    };
    utterance.onend = () => {
      if (generation !== previewGeneration.current || activeUtterance.current !== utterance) return;
      if (browserTimer.current !== null) window.clearTimeout(browserTimer.current);
      browserTimer.current = null;
      activeUtterance.current = null; setPreviewing(false); setPreviewStatus("试听完成");
    };
    utterance.onerror = event => {
      const reasons: Record<string, string> = { "network": "在线音色网络不可达", "voice-unavailable": "音色不可用", "language-unavailable": "缺少语言包", "not-allowed": "浏览器禁止播放", "audio-hardware": "音频输出设备不可用", "synthesis-failed": "设备合成失败" };
      if (event.error !== "canceled" && event.error !== "interrupted") retryOrFail(reasons[event.error] ?? event.error);
    };
    activeUtterance.current = utterance;
    setPreviewStatus(`正在唤起 · ${selected?.name ?? "系统默认音色"}`);
    browserTimer.current = window.setTimeout(() => retryOrFail(), 4000);
    try { synth.resume(); synth.speak(utterance); } catch { retryOrFail(); }
  }

  async function play() {
    if (!settings) return;
    stopPreview(); setError(""); setDiagnostics(null); setSaved(false); setFallbackNotice(""); setPreviewing(true);
    if (settings.provider === "openai-compatible") {
      const controller = new AbortController(); previewRequest.current = controller;
      try {
        setPreviewStatus("正在生成云端音频…");
        const blob = await speechPreview(token, preview, settings, controller.signal);
        if (controller.signal.aborted) return;
        previewUrl.current = URL.createObjectURL(blob);
        const audio = new Audio(previewUrl.current); audioRef.current = audio; audio.volume = settings.volume;
        audio.onended = stopPreview;
        audio.onerror = () => { stopPreview(); setError("音频无法播放，请检查供应商是否返回 MP3 音频。"); };
        setPreviewStatus("正在播放云端音色");
        await audio.play();
      } catch (reason) {
        if (!controller.signal.aborted) {
          stopPreview();
          if (reason instanceof SpeechPreviewError) setDiagnostics(reason.diagnostics);
          else setError(errorText(reason));
        }
      }
      return;
    }
    if (!window.speechSynthesis) { setPreviewing(false); setError("当前浏览器不支持设备语音播报。"); return; }
    const generation = previewGeneration.current;
    window.setTimeout(() => speakBrowserPreview(generation, true), 80);
  }

  if (!settings && !error) return <Spin />;
  const savedVoiceAvailable = !settings?.voice || voices.some(voice => voice.voiceURI === settings.voice || voice.name === settings.voice);
  const deviceVoiceOptions = voices.filter(voice => showOnline || voice.localService)
    .sort((a, b) => Number(b.lang.startsWith("zh")) - Number(a.lang.startsWith("zh")) || Number(b.localService) - Number(a.localService))
    .map(voice => ({ value: voice.voiceURI, label: `${voice.name} · ${voice.lang} · ${voice.localService ? "本机" : "在线 / 依赖网络"}${voiceFailures[voice.voiceURI] ? ` · 最近失败：${voiceFailures[voice.voiceURI]}` : ""}` }));
  if (settings?.voice && !deviceVoiceOptions.some(voice => voice.value === settings.voice)) {
    deviceVoiceOptions.push({ value: settings.voice, label: `${settings.voice} · ${savedVoiceAvailable ? "已选在线音色" : "当前设备不可用"}` });
  }
  return <>
    <ErrorNotice error={error} />
    {diagnostics && <SpeechDiagnostics detail={diagnostics} />}
    {probeResult && <Alert type="success" showIcon message={probeResult} />}
    {fallbackNotice && <Alert type="warning" showIcon message={fallbackNotice} />}
    {saved && <Alert type="success" showIcon message="声音设置已保存，患者端刷新后生效。" />}
    {settings && <section className="knowledge-section speech-settings">
      <div className="section-heading"><div><h2>播报偏好</h2><span>默认声音与音色管理</span></div><Button type="primary" icon={<Save size={16} />} loading={busy} onClick={() => void save()}>保存设置</Button></div>
      <div className="speech-provider-card"><strong>播报方式</strong><Select aria-label="播报方式" value={settings.provider} onChange={value => change({ provider: value })} options={[{ value: "browser", label: "设备语音 · 浏览器音色" }, { value: "openai-compatible", label: "云端语音 · 自定义音色" }]} />
        {settings.provider === "openai-compatible" && <>
          <div className="speech-cloud-summary"><span>{provider?.model || "尚未配置模型"}<small>{provider?.endpoint_host || "在 backend/.env 配置 TTS 服务后重启后端"}</small></span><Button icon={<Activity size={15} />} loading={probing} onClick={() => void probeCloud()}>探活当前音色</Button></div>
          {!!provider?.voice_options?.length && <label>预设音色<Select aria-label="云端预设音色" placeholder="选择预设，也可以在下方输入自定义 ID" value={provider.voice_options.some(item => item.value === settings.cloud_voice) ? settings.cloud_voice : undefined} options={provider.voice_options} onChange={cloud_voice => change({ cloud_voice })} /></label>}
          <label>自定义音色 ID<Input aria-label="云端音色 ID" value={settings.cloud_voice} placeholder={provider?.effective_default_voice || "填入供应商完整音色 ID"} maxLength={300} onChange={event => change({ cloud_voice: event.target.value })} /></label>
          <span className="muted">留空时使用后端默认：{provider?.effective_default_voice || "尚未设置 TTS_VOICE"}。探活与试听使用当前表单，保存后应用到患者端。</span>
          <span className="muted">音色需先在对应供应商创建或获得授权。此处接入已有音色 ID；模型、密钥保存在后端。云端支持语速和播放音量，音调由供应商音色决定。</span>
        </>}
      </div>
      <div className="speech-toggle-row"><div><strong>默认自动播报</strong><p>新回答生成时出声</p></div><Switch aria-label="默认自动播报" checked={settings.auto_play} onChange={auto_play => change({ auto_play })} /></div>
      <div className="speech-form-grid">
        {settings.provider === "browser" && <>
          <label>播报音色<Select aria-label="播报音色" showSearch optionFilterProp="label" value={settings.voice} onChange={voice => { const selected = voices.find(item => item.voiceURI === voice); change({ voice, ...(selected ? { language: selected.lang } : {}) }); }} options={[{ value: "", label: "自动选择本机同语言音色（推荐）" }, ...deviceVoiceOptions]} /></label>
          <div className="device-voice-options"><Switch aria-label="显示在线音色" checked={showOnline} onChange={setShowOnline} /><span>显示在线音色</span><small>已发现 {voices.filter(voice => voice.localService).length} 个本机音色。音色列表不等于可用性检测，请先试听。</small><Button size="small" onClick={() => change({ voice: "", language: "zh-CN" })}>使用自动中文音色</Button></div>
          <label>音色名称 / URI<Input aria-label="音色名称" value={settings.voice} maxLength={300} onChange={event => change({ voice: event.target.value })} /></label>
          <label>语言<Input aria-label="播报语言" value={settings.language} maxLength={35} onChange={event => change({ language: event.target.value })} /></label>
        </>}
        {([['rate', '语速', 0.5, 2], ['pitch', '音调', 0, 2], ['volume', '音量', 0, 1]] as const).filter(([key]) => key !== "pitch" || settings.provider === "browser").map(([key, label, min, max]) => <label key={key}>{label} · {settings[key].toFixed(2)}<Slider aria-label={label} min={min} max={max} step={0.05} value={settings[key]} onChange={value => change({ [key]: value })} /></label>)}
      </div>
      {settings.provider === "browser" && !savedVoiceAvailable && <Alert type="warning" showIcon message="保存的音色在当前设备不可用" description="试听和患者端会自动回退到本机中文音色。建议重新选择标有“本机”的中文音色并保存。" />}
      <div className="voice-preview"><label htmlFor="speech-preview">音色试听</label><Input.TextArea id="speech-preview" value={preview} maxLength={500} onChange={event => setPreview(event.target.value)} autoSize={{ minRows: 2, maxRows: 4 }} /><div className="inline-actions"><Button type="primary" icon={<Volume2 size={16} />} loading={previewing} disabled={!preview.trim() || (settings.provider === "browser" && !("speechSynthesis" in window))} onClick={() => void play()}>试听</Button><Button icon={<Square size={14} />} disabled={!previewing} onClick={stopPreview}>停止</Button>{previewStatus && <span className="muted" role="status">{previewStatus}</span>}</div></div>
      <p className="muted">患者自行设置的静音偏好优先于后台默认值。设备语音在指定音色不可用时选择同语言音色；云端语音在各设备使用同一个音色 ID。</p>
    </section>}
  </>;
}
