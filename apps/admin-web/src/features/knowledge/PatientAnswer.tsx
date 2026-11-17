import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { patientVisibleMarkdown } from "./patientMarkdown";

export function PatientAnswer({ content }: { content: string }) {
  return <div className="patient-answer-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{patientVisibleMarkdown(content)}</ReactMarkdown>
  </div>;
}
