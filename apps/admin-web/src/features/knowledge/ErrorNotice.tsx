import { Alert } from "antd";

export function ErrorNotice({ error }: { error?: string }) {
  return error ? <Alert type="error" showIcon message={error} /> : null;
}
