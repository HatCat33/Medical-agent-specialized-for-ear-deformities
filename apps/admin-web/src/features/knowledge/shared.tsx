import { useEffect, useState } from "react";
import { ApiError, knowledgeApi } from "../../lib/api";

export const labels: Record<string, string> = {
  draft: "草稿", published: "已发布", archived: "历史版本",
  queued: "排队中", running: "执行中", completed: "已完成", failed: "失败", interrupted: "已中断",
  embedding: "向量构建", evaluation: "检索评测", draft_created: "创建草稿",
  entry_created: "新增条目", entry_updated: "更新条目", entry_deleted: "删除条目",
  xlsx_merged: "增量导入", release_published: "发布版本", draft_deleted: "删除草稿",
};
const messages: Record<string, string> = {
  create_draft_before_editing: "请先复制为草稿，再修改内容或全量重建。",
  knowledge_job_active: "该版本正在构建向量，请等待任务结束。",
  duplicate_question: "该标准问题已存在，请编辑已有条目。",
  cannot_publish_empty_release: "空版本不能发布。",
  embedding_model_changed_rebuild_draft: "Embedding 模型已变更，请在草稿中全量重建。",
  full_rebuild_required: "上次全量重建未完成，请重新执行全量重建后再发布。",
  invalid_faq_xlsx: "文件无法解析。请检查 XLSX 格式及分类、标准问题、答案内容列。",
  faq_has_invalid_rows: "表格存在空问题、空答案或缺少分类的行，请修正后导入。",
  faq_conflicting_duplicate_answers: "文件中同一问题有不同答案，请先处理冲突。",
  expected_entry_not_in_release: "评测的预期 chunk ID 不属于当前版本，请重新选择。",
  xlsx_too_large: "文件超过限制：压缩前 10 MB、解压后 50 MB。",
};
export function errorText(error: unknown) {
  return error instanceof ApiError ? (messages[error.code] ?? error.message)
    : error instanceof Error ? error.message : "请求失败，请重试";
}
export function useAdminData<T>(token: string, path: string | null, revision = 0) {
  const [state, setState] = useState<{ data?: T; error?: string }>({});
  useEffect(() => {
    if (!path) return;
    let active = true;
    void knowledgeApi<T>(token, path).then(data => { if (active) setState({ data }); })
      .catch((error: unknown) => { if (active) setState({ error: errorText(error) }); });
    return () => { active = false; };
  }, [token, path, revision]);
  return state;
}
export function downloadJson(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
