import { describe, expect, it } from "vitest";

import { patientVisibleMarkdown } from "./patientMarkdown";

describe("patient answer", () => {
  it("matches the patient view by hiding citations but preserving answer markdown", () => {
    expect(patientVisibleMarkdown(
      "**建议复诊**，并继续观察。[1]\n\n参考资料：\n- [1] 医院 FAQ",
    )).toBe("**建议复诊**，并继续观察。");
  });
});
