export type AdminSection = "overview" | "versions" | "chunks" | "tests" | "prompts" | "speech" | "operations";
export const sectionNames: Record<AdminSection, string> = {
  overview: "运行总览", versions: "知识版本", chunks: "分块管理", tests: "检索与评测",
  prompts: "提示词管理", speech: "声音设置", operations: "任务与记录",
};
