const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
const SECONDARY_AUTHORIZATION_KEY = "medical-agent-admin-authorization";
const SECONDARY_AUTHORIZATION_EXPIRY_KEY = "medical-agent-admin-authorization-expiry";
let secondaryVerificationHandler: (() => Promise<void>) | null = null;

export interface SecondarySecurityStatus {
  configured: boolean;
  verified: boolean;
  ttl_seconds: number;
}

export function configureSecondaryVerification(handler: (() => Promise<void>) | null) {
  secondaryVerificationHandler = handler;
}

export function secondaryAuthorizationExpiry(): string {
  return sessionStorage.getItem(SECONDARY_AUTHORIZATION_EXPIRY_KEY) ?? "";
}

export function clearSecondaryAuthorization() {
  sessionStorage.removeItem(SECONDARY_AUTHORIZATION_KEY);
  sessionStorage.removeItem(SECONDARY_AUTHORIZATION_EXPIRY_KEY);
}

export interface KnowledgeReleaseStatus {
  id: string;
  name: string;
  source_name: string;
  status: string;
  entry_count: number;
  vectorized_count: number;
  pending_count: number;
  embedding_configured: boolean;
  embedding_model: string;
}

export interface EmbeddingRebuildResponse {
  release_id: string;
  total_entries: number;
  existing_entries: number;
  processed_entries: number;
  remaining_entries: number;
  vectorized_count: number;
  vector_dimension: number | null;
}

const errorMessages: Record<string, string> = {
  admin_api_not_configured: "后端尚未配置管理员令牌",
  admin_token_invalid: "管理员令牌不正确",
  admin_secondary_token_not_configured: "后端尚未配置二次验证令牌",
  admin_secondary_token_invalid: "二次验证令牌不正确",
  admin_secondary_verification_required: "该操作需要先完成二次验证",
  published_knowledge_release_not_found: "当前没有已发布的知识版本",
  knowledge_release_not_found: "知识版本不存在",
  embedding_not_configured: "Embedding 服务配置不完整",
  embedding_dimension_invalid: "Embedding 服务返回的向量维度无效",
  embedding_dimension_mismatch: "新向量与已有向量维度不一致",
  embedding_request_failed: "Embedding 服务请求失败",
  embedding_count_mismatch: "Embedding 服务返回的向量数量与文本数量不一致",
  embedding_response_invalid: "Embedding 服务返回格式无效",
  knowledge_draft_limit_reached: "知识草稿数量已达到上限，请先发布或删除现有草稿",
  reranker_request_failed: "Rerank 服务请求失败",
  reranker_response_invalid: "Rerank 服务返回格式无效",
  xlsx_required: "请上传 .xlsx 格式的 Excel 文件",
  invalid_faq_xlsx: "文件无法解析。请检查 XLSX 文件是否损坏，以及首个工作表是否包含：分类、标准问题、答案内容",
  faq_has_invalid_rows: "表格中存在缺少分类、标准问题或答案内容的非空行，请修正后重新导入",
  faq_conflicting_duplicate_answers: "文件中同一标准问题对应了不同答案，请先合并或修改",
  xlsx_changed_after_preview: "文件在预览后发生变化，请重新上传并审核",
  knowledge_draft_changed_after_preview: "草稿内容在审核期间发生变化，请重新上传并审核后再合并",
  invalid_import_selection: "审核选择无效，请重新选择要合并的内容",
  xlsx_too_large: "文件超过限制：上传文件最大 10 MB，解压后最大 50 MB",
  prompt_release_not_found: "提示词版本不存在",
  prompt_release_not_editable: "仅草稿提示词可以编辑",
  prompt_release_not_deletable: "仅草稿提示词可以删除",
  prompt_draft_limit_reached: "提示词草稿数量已达到上限，请先发布或删除现有草稿",
  announcement_not_found: "公告不存在",
  announcement_not_editable: "只有公告草稿可以编辑",
  announcement_not_publishable: "只有公告草稿可以发布",
  announcement_not_deletable: "只有公告草稿可以删除",
  announcement_draft_limit_reached: "公告草稿数量已达到上限，请先发布或删除现有草稿",
  invalid_user_reference: "用户 ID 格式不正确，请输入患者端显示的 16 位用户 ID",
  invalid_date_range: "开始日期不能晚于结束日期",
  interaction_export_empty: "当前筛选条件下没有可导出的完整问答",
  model_not_configured: "该模型服务尚未配置完整",
  probe_timeout: "模型探活超时",
  probe_failed: "模型探活失败",
  tts_not_configured: "请先在后端配置 TTS_BASE_URL、TTS_API_KEY 和 TTS_MODEL，再重启后端",
  tts_voice_required: "请填写云端音色 ID，或在后端配置 TTS_VOICE",
  tts_request_failed: "云端语音请求失败，请在运行总览中探活查看原因",
  tts_timeout: "云端语音生成超时",
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
  }
}

async function adminFetch(path: string, token: string, init?: RequestInit, retry = true): Promise<Response> {
  const authorization = sessionStorage.getItem(SECONDARY_AUTHORIZATION_KEY) ?? "";
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      "X-Admin-Token": token,
      ...(authorization ? { "X-Admin-Authorization": authorization } : {}),
      ...init?.headers,
    },
  });
  if (response.status === 403 && retry && secondaryVerificationHandler) {
    const payload = (await response.clone().json().catch(() => null)) as { detail?: unknown } | null;
    if (payload?.detail === "admin_secondary_verification_required") {
      clearSecondaryAuthorization();
      await secondaryVerificationHandler();
      return adminFetch(path, token, init, false);
    }
  }
  return response;
}

export async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await adminFetch(path, token, init);
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const code = typeof payload?.detail === "string" ? payload.detail : `http_${response.status}`;
    const requestId = response.headers.get("x-request-id");
    const message = errorMessages[code] ?? (response.status === 422 ? "填写的参数不符合要求，请检查后重试" : `管理服务请求失败（HTTP ${response.status}）${requestId ? `，请求编号：${requestId}` : ""}`);
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function secondarySecurityStatus(token: string): Promise<SecondarySecurityStatus> {
  return request<SecondarySecurityStatus>("/admin/security/secondary", token);
}

export async function verifySecondaryToken(token: string, secondaryToken: string): Promise<{ authorization: string; expires_at: string }> {
  const response = await fetch(`${API_BASE}/admin/security/secondary/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Admin-Token": token },
    body: JSON.stringify({ token: secondaryToken }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const code = typeof payload?.detail === "string" ? payload.detail : `http_${response.status}`;
    throw new ApiError(errorMessages[code] ?? "二次验证失败，请稍后重试", response.status, code);
  }
  const result = await response.json() as { authorization: string; expires_at: string };
  sessionStorage.setItem(SECONDARY_AUTHORIZATION_KEY, result.authorization);
  sessionStorage.setItem(SECONDARY_AUTHORIZATION_EXPIRY_KEY, result.expires_at);
  return result;
}

export function getCurrentRelease(token: string): Promise<KnowledgeReleaseStatus> {
  return request("/admin/knowledge/releases/current", token);
}

export const knowledgeApi = <T>(token: string, path: string, init?: RequestInit) =>
  request<T>(`/admin/knowledge${path}`, token, init);

export async function exportInteractionRecords(token: string, query: string): Promise<{ blob: Blob; filename: string }> {
  const response = await adminFetch(`/admin/knowledge/interaction-analytics/export?${query}`, token);
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const code = typeof payload?.detail === "string" ? payload.detail : `http_${response.status}`;
    throw new ApiError(errorMessages[code] ?? "导出交互记录失败，请稍后重试", response.status, code);
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const filename = encodedName ? decodeURIComponent(encodedName) : `用户交互记录-${Date.now()}.xlsx`;
  return { blob: await response.blob(), filename };
}

export const systemApi = <T>(token: string, path: string, init?: RequestInit) =>
  request<T>(`/admin/system${path}`, token, init);

export interface Overview {
  rag_enabled: boolean; database: string; embedding_configured: boolean;
  redis_enabled: boolean; redis_connected: boolean; embedding_cache_enabled: boolean;
  retrieval_cache_enabled: boolean; tts_cache_enabled: boolean;
  embedding_model: string; rag_top_k: number; rag_min_score: number;
  rag_relative_score: number; chunk_strategy: string; vector_storage: string;
  interaction_count?: number; active_jobs: number;
  reranker_enabled: boolean; reranker_configured: boolean; reranker_model: string;
  knowledge_draft_limit: number; knowledge_draft_count: number;
}
export interface RetrievalConfiguration {
  top_k: number; min_score: number; relative_score: number;
}
export interface InteractionAnalytics {
  summary: {
    total_users: number;
    matched_users: number;
    matched_interactions: number;
    average_interactions: number;
  };
  trend: Array<{ key: string; label: string; interactions: number; users: number }>;
  top_users: Array<{ user_id: string; interactions: number }>;
  items: Array<{
    id: string;
    user_id: string;
    conversation_id: string;
    conversation_title: string;
    input: string;
    output: string;
    status: string;
    created_at: string;
  }>;
  page: number;
  page_size: number;
  total: number;
}
export interface Release {
  id: string; name: string; source_name: string; status: string;
  entry_count: number; vectorized_count: number; pending_count: number;
  created_at: string; published_at: string | null; quality_report: Record<string, unknown>;
}
export interface EntryEdit {
  category: string; question: string; aliases: string; answer: string; effective_date: string;
}
export interface Entry extends EntryEdit {
  id: string; release_id: string; source_sheet: string; source_row: number;
  vectorized: boolean; chunk_text: string;
}
export type ImportRowKind = "added" | "updated" | "unchanged" | "conflict";
export interface ImportPreviewRow {
  key: string;
  source_row: number;
  kind: ImportRowKind;
  conflict_rows: number[];
  changed_fields: Array<keyof EntryEdit>;
  incoming: EntryEdit;
  existing: Entry | null;
}
export interface ImportPreview {
  source_name: string;
  checksum: string;
  draft_checksum: string;
  warnings: string[];
  counts: Record<ImportRowKind, number>;
  rows: ImportPreviewRow[];
}
export interface Operation {
  id: string; release_id: string; kind: string; status: string;
  processed: number; total: number; detail: Record<string, unknown>;
  error: string; created_at: string; finished_at: string | null;
}
export interface SearchHit {
  entry_id: string; question: string; answer: string; category: string;
  source_name: string; source_sheet: string; source_row: number;
  score: number; lexical: number; semantic: number | null; rerank: number | null;
}
export interface SearchResult {
  query: string; mode: string; warnings: string[]; latency_ms: number; hits: SearchHit[];
}

export interface PromptRelease {
  id: string; name: string; content: string; system_content: string; checksum: string; status: string;
  created_at: string; published_at: string | null;
}
export interface PromptInventory {
  items: PromptRelease[]; effective: PromptRelease | null; effective_content: string;
  using_default: boolean; safety_baseline: string; effective_system_content: string;
  draft_limit: number; draft_count: number;
}
export interface Announcement {
  id: string; title: string; content: string; status: "draft" | "published";
  created_at: string; updated_at: string; published_at: string | null;
}
export interface AnnouncementInventory {
  items: Announcement[]; draft_count: number; draft_limit: number;
}

export const announcementApi = <T>(token: string, path = "", init?: RequestInit) =>
  request<T>(`/admin/announcements${path}`, token, init);
export interface SpeechSettings {
  auto_play: boolean; voice: string; language: string; rate: number; pitch: number; volume: number;
  provider: "browser" | "openai-compatible"; cloud_voice: string;
}

export interface SpeechProviderInfo {
  protocol?: string;
  configured: boolean; model: string; default_voice: string; endpoint_host: string;
  effective_default_voice: string; voice_options: { label: string; value: string }[];
}

export class SpeechPreviewError extends Error {
  constructor(readonly diagnostics: Record<string, unknown>) {
    super(String(diagnostics.summary ?? "云端试听失败"));
  }
}

export async function speechPreview(token: string, text: string, settings: SpeechSettings, signal: AbortSignal): Promise<Blob> {
  const response = await adminFetch("/admin/system/speech/preview", token, {
    method: "POST", signal, headers: { "Content-Type": "application/json", "X-Admin-Token": token },
    body: JSON.stringify({ text, settings }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: string | Record<string, unknown> };
    if (payload.detail && typeof payload.detail === "object") throw new SpeechPreviewError(payload.detail);
    throw new Error(errorMessages[payload.detail ?? ""] ?? "试听失败，请检查语音服务配置");
  }
  return response.blob();
}
export interface ModelProbe {
  id: string; kind: string; target_id: string; status: string; latency_ms: number | null;
  detail: Record<string, unknown>; error: string; created_at: string;
}
export interface ModelConfig {
  kind: string; label: string; provider: string; model: string; endpoint_host: string;
  api_key_configured: boolean; configured: boolean; client_only?: boolean;
  active?: boolean;
  settings: Record<string, unknown>; last_probe: ModelProbe | null;
}
export interface ModelInventory { items: ModelConfig[]; note: string }
export interface ModelConfigurationItem {
  kind: string; values: Record<string, string | number | boolean | null>; api_key_configured: boolean;
}
export interface ModelConfigurationInventory {
  items: ModelConfigurationItem[]; overridden_kinds: string[];
}

export interface CapacityMetric {
  scope: string; label: string; limit: number; wait_seconds: number;
  active: number; waiting: number; utilization_percent: number | null;
  total_acquired: number; total_rejected: number; recent_samples: number;
  average_wait_ms: number | null; p50_wait_ms: number | null; p95_wait_ms: number | null;
  max_wait_ms: number | null; last_wait_ms: number | null; last_activity_at: string | null;
}
export interface CapacityInventory {
  items: CapacityMetric[]; sampled_at: string; window_seconds: number;
  process_id: number; redis_connected: boolean;
}

export function rebuildEmbeddings(
  token: string,
  releaseId: string,
  options: { batchSize: number; rebuildAll: boolean },
): Promise<EmbeddingRebuildResponse> {
  return request(`/admin/knowledge/releases/${releaseId}/embeddings/rebuild`, token, {
    method: "POST",
    body: JSON.stringify({
      batch_size: options.batchSize,
      rebuild_all: options.rebuildAll,
    }),
  });
}
