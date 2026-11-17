import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const workspaceRoot = fileURLToPath(new URL("../../", import.meta.url));
  const env = loadEnv(mode, workspaceRoot, "");
  return {
    plugins: [react()],
    envDir: workspaceRoot,
    server: {
      port: 5174,
      proxy: {
        "/api": env.VITE_BACKEND_PROXY_TARGET || "http://127.0.0.1:8000",
      },
    },
  };
});

