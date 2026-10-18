import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const workspaceRoot = fileURLToPath(new URL("../../", import.meta.url));
  const env = loadEnv(mode, workspaceRoot, "");
  const certificatePath = env.VITE_HTTPS_CERT_PATH;
  const keyPath = env.VITE_HTTPS_KEY_PATH;
  if (Boolean(certificatePath) !== Boolean(keyPath)) {
    throw new Error("VITE_HTTPS_CERT_PATH and VITE_HTTPS_KEY_PATH must be configured together.");
  }
  const https =
    certificatePath && keyPath
      ? {
          cert: readFileSync(path.resolve(certificatePath)),
          key: readFileSync(path.resolve(keyPath)),
        }
      : undefined;

  return {
    plugins: [react()],
    server: {
      port: 5173,
      https,
      proxy: {
        "/api": "http://127.0.0.1:8000",
      },
    },
  };
});

