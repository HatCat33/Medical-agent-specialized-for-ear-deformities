import { Alert } from "antd";

export function SpeechDiagnostics({ detail }: { detail: Record<string, unknown> }) {
  return <Alert type="error" showIcon message={String(detail.summary ?? "云端语音不可用")}
    description={<div className="speech-diagnostics">
      {!!detail.suggestion && <p>{String(detail.suggestion)}</p>}
      {!!detail.model && <p>模型：{String(detail.model)}</p>}
      {!!detail.voice && <p>实际音色 ID：<code>{String(detail.voice)}</code></p>}
      {detail.http_status != null && <p>供应商 HTTP：{String(detail.http_status)}</p>}
      {!!detail.upstream_message && <p>供应商说明：{String(detail.upstream_message)}</p>}
      {!!detail.upstream_code && <p>错误码：{String(detail.upstream_code)}</p>}
      {!!detail.request_id && <p>请求编号：{String(detail.request_id)}</p>}
    </div>} />;
}
