import { api, type WeComJsSdkConfiguration } from "./api";

type SourceType = "album" | "camera";

interface WeComSdk {
  config(options: Record<string, unknown>): void;
  ready(callback: () => void): void;
  error(callback: (error: unknown) => void): void;
  agentConfig?(options: {
    corpid: string;
    agentid: string;
    timestamp: number;
    nonceStr: string;
    signature: string;
    jsApiList: string[];
    success(): void;
    fail(error: unknown): void;
  }): void;
  checkJsApi?(options: {
    jsApiList: string[];
    success(result: { checkResult?: Record<string, boolean | string> }): void;
    fail?(error: unknown): void;
  }): void;
  chooseImage(options: {
    count: number;
    sizeType: string[];
    sourceType: SourceType[];
    success(result: { localIds?: string[] }): void;
    cancel?(): void;
    fail?(error: unknown): void;
  }): void;
  getLocalImgData(options: {
    localId: string;
    success(result: { localData?: string }): void;
    fail?(error: unknown): void;
  }): void;
}

declare global {
  interface Window { wx?: WeComSdk }
}

let baseScriptPromise: Promise<void> | null = null;
let workScriptPromise: Promise<void> | null = null;
let configuredUrl = "";
let configurationPromise: Promise<WeComSdk> | null = null;

export function isEnterpriseWeChat(): boolean {
  return /wxwork/i.test(navigator.userAgent);
}

function loadScript(source: string, current: Promise<void> | null): Promise<void> {
  if (current) return current;
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = source;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("wecom_sdk_load_failed"));
    document.head.append(script);
  });
}

async function loadSdk(): Promise<WeComSdk> {
  if (!window.wx) {
    baseScriptPromise = loadScript(
      "https://res.wx.qq.com/open/js/jweixin-1.6.0.js",
      baseScriptPromise,
    ).catch(error => {
      baseScriptPromise = null;
      throw error;
    });
    await baseScriptPromise;
  }
  if (!window.wx?.agentConfig) {
    workScriptPromise = loadScript(
      "https://open.work.weixin.qq.com/wwopen/js/jwxwork-1.0.0.js",
      workScriptPromise,
    ).catch(error => {
      workScriptPromise = null;
      throw error;
    });
    await workScriptPromise;
  }
  if (!window.wx) throw new Error("wecom_sdk_missing");
  return window.wx;
}

async function configuredSdk(): Promise<WeComSdk> {
  const pageUrl = window.location.href.split("#", 1)[0];
  if (configuredUrl === pageUrl && window.wx) return window.wx;
  if (configurationPromise) return configurationPromise;
  configurationPromise = (async () => {
    const [sdk, configuration] = await Promise.all([
      loadSdk(),
      api.wecomJsSdkConfiguration(pageUrl),
    ]);
    await configure(sdk, configuration);
    configuredUrl = pageUrl;
    return sdk;
  })().finally(() => { configurationPromise = null; });
  return configurationPromise;
}

async function configure(sdk: WeComSdk, value: WeComJsSdkConfiguration): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("wecom_sdk_config_timeout")), 8000);
    sdk.ready(() => { window.clearTimeout(timer); resolve(); });
    sdk.error((error) => { window.clearTimeout(timer); reject(new Error(`wecom_sdk_config_failed:${errorDetail(error)}`)); });
    sdk.config({
      beta: true,
      debug: false,
      appId: value.corp_id,
      timestamp: value.timestamp,
      nonceStr: value.nonce,
      signature: value.signature,
      jsApiList: ["chooseImage", "getLocalImgData"],
    });
  });
  if (!sdk.agentConfig) throw new Error("wecom_agent_config_missing");
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("wecom_agent_config_timeout")), 8000);
    sdk.agentConfig?.({
      corpid: value.corp_id,
      agentid: value.agent_id,
      timestamp: value.timestamp,
      nonceStr: value.nonce,
      signature: value.agent_signature,
      jsApiList: ["chooseImage", "getLocalImgData"],
      success: () => { window.clearTimeout(timer); resolve(); },
      fail: error => {
        window.clearTimeout(timer);
        reject(new Error(`wecom_agent_config_failed:${errorDetail(error)}`));
      },
    });
  });
  await verifyImageApis(sdk);
}

function errorDetail(error: unknown): string {
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const message = (error as { errMsg?: unknown }).errMsg;
    if (typeof message === "string") return message;
    try { return JSON.stringify(error); } catch { return "unknown"; }
  }
  return String(error);
}

function verifyImageApis(sdk: WeComSdk): Promise<void> {
  if (!sdk.checkJsApi) return Promise.resolve();
  return new Promise((resolve, reject) => {
    sdk.checkJsApi?.({
      jsApiList: ["chooseImage", "getLocalImgData"],
      success: result => {
        const checks = result.checkResult ?? {};
        const unavailable = ["chooseImage", "getLocalImgData"].filter(name => {
          const value = checks[name];
          return value === false || value === "false";
        });
        if (unavailable.length) reject(new Error(`wecom_image_api_unavailable:${unavailable.join(",")}`));
        else resolve();
      },
      fail: error => reject(new Error(`wecom_check_js_api_failed:${errorDetail(error)}`)),
    });
  });
}

function chooseLocalIds(sdk: WeComSdk, count: number, sourceType: SourceType): Promise<string[]> {
  return new Promise((resolve, reject) => {
    sdk.chooseImage({
      count,
      sizeType: ["original", "compressed"],
      sourceType: [sourceType],
      success: result => resolve(Array.isArray(result.localIds) ? result.localIds.slice(0, count) : []),
      cancel: () => resolve([]),
      fail: error => reject(new Error(`wecom_choose_image_failed:${errorDetail(error)}`)),
    });
  });
}

function localImageData(sdk: WeComSdk, localId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    sdk.getLocalImgData({
      localId,
      success: result => result.localData
        ? resolve(result.localData)
        : reject(new Error("wecom_local_image_empty")),
      fail: error => reject(new Error(`wecom_local_image_failed:${errorDetail(error)}`)),
    });
  });
}

function dataUrlFile(value: string, index: number): File {
  const normalized = (value.startsWith("data:")
    ? value
    : `data:image/jpeg;base64,${value.replaceAll(/\s/g, "")}`)
    .replace(/^data:image\/jgp/i, "data:image/jpeg");
  const separator = normalized.indexOf(",");
  if (separator < 0) throw new Error("wecom_local_image_invalid");
  const header = normalized.slice(0, separator);
  const mime = /^data:([^;]+)/.exec(header)?.[1] ?? "image/jpeg";
  const binary = atob(normalized.slice(separator + 1).replaceAll(/\s/g, ""));
  const bytes = new Uint8Array(binary.length);
  for (let position = 0; position < binary.length; position += 1) bytes[position] = binary.charCodeAt(position);
  const extension = mime.includes("png") ? "png" : mime.includes("webp") ? "webp" : "jpg";
  return new File([bytes], `wecom-${Date.now()}-${index + 1}.${extension}`, { type: mime });
}

export async function chooseEnterpriseWeChatImages(count: number, sourceType: SourceType): Promise<File[]> {
  const sdk = await configuredSdk();
  const localIds = await chooseLocalIds(sdk, Math.max(1, Math.min(9, count)), sourceType);
  return Promise.all(localIds.map(async (localId, index) => dataUrlFile(await localImageData(sdk, localId), index)));
}
