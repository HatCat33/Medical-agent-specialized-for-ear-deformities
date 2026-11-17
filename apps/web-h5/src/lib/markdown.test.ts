import { describe, expect, it } from "vitest";

import { patientVisibleMarkdown } from "./markdown";

describe("patientVisibleMarkdown", () => {
  it("hides the generated reference appendix and inline reference numbers", () => {
    const answer =
      "建议携带已有检查资料[1]，并提前整理问题 [2]。\n\n参考资料：\n- [1] 医院知识库 · 就诊准备\n- [2] 医院知识库 · 门诊流程";

    expect(patientVisibleMarkdown(answer)).toBe("建议携带已有检查资料，并提前整理问题。");
  });

  it("also hides a markdown reference heading", () => {
    const answer = "这是患者可见内容。\n\n### 参考资料\n- [1] 内部资料";

    expect(patientVisibleMarkdown(answer)).toBe("这是患者可见内容。");
  });

  it("does not change an answer without a reference appendix", () => {
    const answer = "术后第 [1] 天请按照医生要求护理。";

    expect(patientVisibleMarkdown(answer)).toBe(answer);
  });
});
