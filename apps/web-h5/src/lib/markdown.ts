const REFERENCE_SECTION = /\n{2,}(?:#{1,6}\s*)?参考资料[：:]?\s*(?:\r?\n|$)/;
const INLINE_REFERENCE = /\s*\[(?:\d+(?:\s*[-,，]\s*\d+)*)\]/g;

export function patientVisibleMarkdown(value: string): string {
  const referenceSection = REFERENCE_SECTION.exec(value);
  if (!referenceSection || referenceSection.index === undefined) return value;

  return value
    .slice(0, referenceSection.index)
    .replace(INLINE_REFERENCE, "")
    .trimEnd();
}
