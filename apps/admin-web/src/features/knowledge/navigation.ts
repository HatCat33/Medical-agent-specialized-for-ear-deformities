export type AdminSection = "overview" | "knowledge" | "tests" | "prompts" | "announcements";
export const sectionNames: Record<AdminSection, string> = {
  overview: "运行总览", knowledge: "知识库管理", tests: "检索与评测",
  prompts: "提示词管理", announcements: "公告管理",
};
