const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

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
  published_knowledge_release_not_found: "当前没有已发布的知识版本",
  knowledge_release_not_found: "知识版本不存在",
  embedding_not_configured: "Embedding 服务配置不完整",
  embedding_dimension_invalid: "Embedding 服务返回的向量维度无效",
  embedding_dimension_mismatch: "新向量与已有向量维度不一致",
  embedding_request_failed: "Embedding 服务请求失败",
  embedding_count_mismatch: "Embedding 服务返回的向量数量与文本数量不一致",
  embedding_response_invalid: "Embedding 服务返回格式无效",
  xlsx_required: "请上传 .xlsx 格式的 Excel 文件",
  invalid_faq_xlsx: "文件无法解析。请检查 XLSX 文件是否损坏，以及首个工作表是否包含：分类、标准问题、答案内容",
  faq_has_invalid_rows: "表格中存在缺少分类、标准问题或答案内容的非空行，请修正后重新导入",
  faq_conflicting_duplicate_answers: "文件中同一标准问题对应了不同答案，请先合并或修改",
  xlsx_too_large: "文件超过限制：上传文件最大 10 MB，解压后最大 50 MB",
  prompt_release_not_found: "提示词版本不存在",
  prompt_release_not_editable: "仅草稿提示词可以编辑",
  prompt_release_not_deletable: "仅草稿提示词可以删除",
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

export async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      "X-Admin-Token": token,
      ...init?.headers,
    },
  });
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

export function getCurrentRelease(token: string): Promise<KnowledgeReleaseStatus> {
  return request("/admin/knowledge/releases/current", token);
}

export const knowledgeApi = <T>(token: string, path: string, init?: RequestInit) =>
  request<T>(`/admin/knowledge${path}`, token, init);

export const systemApi = <T>(token: string, path: string, init?: RequestInit) =>
  request<T>(`/admin/system${path}`, token, init);

export interface Overview {
  rag_enabled: boolean; database: string; embedding_configured: boolean;
  embedding_model: string; rag_top_k: number; rag_min_score: number;
  rag_relative_score: number; chunk_strategy: string; vector_storage: string;
  interaction_count?: number; active_jobs: number;
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
export interface Operation {
  id: string; release_id: string; kind: string; status: string;
  processed: number; total: number; detail: Record<string, unknown>;
  error: string; created_at: string; finished_at: string | null;
}
export interface SearchHit {
  entry_id: string; question: string; answer: string; category: string;
  source_name: string; source_sheet: string; source_row: number;
  score: number; lexical: number; semantic: number | null;
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
}
export interface SpeechSettings {
  auto_play: boolean; voice: string; language: string; rate: number; pitch: number; volume: number;
  provider: "browser" | "openai-compatible"; cloud_voice: string;
}

export interface SpeechProviderInfo {
  configured: boolean; model: string; default_voice: string; endpoint_host: string;
  effective_default_voice: string; voice_options: { label: string; value: string }[];
}

export class SpeechPreviewError extends Error {
  constructor(readonly diagnostics: Record<string, unknown>) {
    super(String(diagnostics.summary ?? "云端试听失败"));
  }
}

export async function speechPreview(token: string, text: string, settings: SpeechSettings, signal: AbortSignal): Promise<Blob> {
  const response = await fetch(`${API_BASE}/admin/system/speech/preview`, {
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
