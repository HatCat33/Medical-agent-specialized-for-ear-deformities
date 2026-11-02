import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.ADMIN_UI_BASE_URL ?? "http://127.0.0.1:5175";
const outputDir = path.resolve(".artifacts", "visual-check");
const executablePath = [
  process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean).find(candidate => existsSync(candidate));
const now = new Date().toISOString();
const release = { id: "release-1", name: "医院 FAQ", source_name: "faq.xlsx", status: "published", entry_count: 12, vectorized_count: 12, pending_count: 0, created_at: now, published_at: now, quality_report: {} };
const model = (kind, label, name) => ({ kind, label, provider: "openai-compatible", model: name, endpoint_host: "https://api.example.com", api_key_configured: true, configured: true, client_only: false, settings: { timeout_seconds: 60 }, last_probe: null });
const models = [model("chat", "文本对话", "qwen-chat"), model("vision", "图片理解", "qwen-vl"), model("asr", "语音识别", "asr-model"), model("embedding", "文本向量", "embedding-model"), model("tts", "云端语音合成", "tts-model"), { ...model("browser_tts", "设备语音", "Web Speech Synthesis"), provider: "browser", client_only: true }];
const configurations = models.filter(item => !item.client_only).map(item => ({ kind: item.kind, api_key_configured: true, values: { ...(item.kind === "chat" || item.kind === "asr" ? { provider: "openai-compatible" } : {}), base_url: "https://api.example.com/v1", model: item.model, timeout_seconds: 60, ...(item.kind === "chat" || item.kind === "vision" ? { max_tokens: 1024, enable_thinking: false } : {}), ...(item.kind === "embedding" ? { batch_size: 32 } : {}), ...(item.kind === "asr" ? { language: "zh" } : {}), ...(item.kind === "tts" ? { voice: "default" } : {}) } }));
const hit = { entry_id: "chunk-1", question: "术后多久可以侧睡", answer: "需要结合手术方式和恢复情况，由复诊医生确认。恢复早期通常应避免压迫术区。", category: "术后护理", source_name: "faq.xlsx", source_sheet: "FAQ", source_row: 8, score: 0.92, lexical: 0.84, semantic: 0.95 };

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch(executablePath ? { executablePath } : {});
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page = await context.newPage();
  let configurationAvailable = false;
  let speech = { provider: "openai-compatible", cloud_voice: "", voice: "", language: "zh-CN", auto_play: true, rate: 1, pitch: 1, volume: 1 };
  let savedModel = null;
  await page.route("**/api/v1/**", route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname.endsWith("/admin/system/speech/provider")) return route.fulfill({ json: { protocol: "openai-compatible", model: "tts-model", endpoint_host: "https://api.example.com", effective_default_voice: "default", voice_options: [] } });
    if (pathname.endsWith("/admin/system/speech")) {
      if (request.method() === "PUT") speech = request.postDataJSON();
      return route.fulfill({ json: speech });
    }
    if (pathname.endsWith("/admin/system/speech/preview")) return route.fulfill({ status: 502, json: { detail: { summary: "供应商拒绝请求参数", suggestion: "检查音色 ID", http_status: 400, upstream_message: "Invalid voice." } } });
    if (pathname.endsWith("/admin/system/models/tts/configuration") && request.method() === "PUT") {
      savedModel = request.postDataJSON();
      return route.fulfill({ json: { kind: "tts", values: savedModel, api_key_configured: true } });
    }
    if (pathname.endsWith("/admin/knowledge/overview")) return route.fulfill({ json: { rag_enabled: true, database: "sqlite", embedding_configured: true, embedding_model: "embedding-model", rag_top_k: 4, rag_min_score: 0.16, rag_relative_score: 0.62, chunk_strategy: "FAQ 条目", vector_storage: "SQLite", interaction_count: 128, active_jobs: 0 } });
    if (pathname.endsWith("/admin/knowledge/releases")) return route.fulfill({ json: [release] });
    if (pathname.endsWith("/admin/system/models/configuration") && request.method() === "GET") return configurationAvailable
      ? route.fulfill({ json: { items: configurations, overridden_kinds: ["chat"] } })
      : route.fulfill({ status: 404, json: { detail: "not_found" } });
    if (pathname.endsWith("/admin/system/models")) return route.fulfill({ json: { items: models, note: "" } });
    if (pathname.endsWith("/admin/knowledge/search")) return route.fulfill({ json: { query: "术后多久可以侧睡", mode: "hybrid", warnings: [], latency_ms: 38, hits: [hit] } });
    if (pathname.endsWith("/admin/knowledge/conversation-tests")) return route.fulfill({ json: { id: "test-1", turn_count: 2, duration_ms: 1250, turns: [
      { turn: 1, input: "孩子术后多久可以侧睡？", output: "恢复早期应避免压迫术区，具体时间需要结合手术方式和复诊情况确认。", status: "completed", safety_code: null, duration_ms: 620, rag_enabled: true, knowledge_hits: [{ entry_id: hit.entry_id, question: hit.question, category: hit.category, score: hit.score }], model: "qwen-chat", prompt_release_id: "prompt-1", memory_through_sequence: 0, memory_summary_length: 0 },
      { turn: 2, input: "如果睡着后不小心碰到了呢？", output: "可以先观察术区有没有持续疼痛、出血、渗液或颜色变化；出现这些情况时联系手术医院。", status: "completed", safety_code: null, duration_ms: 630, rag_enabled: true, knowledge_hits: [], model: "qwen-chat", prompt_release_id: "prompt-1", memory_through_sequence: 0, memory_summary_length: 0 },
    ] } });
    return route.fulfill({ status: 404, json: { detail: "not_found" } });
  });
  await page.goto(baseUrl);
  await page.getByLabel("管理员令牌").fill("mock-admin");
  await page.getByRole("button", { name: "进入管理端" }).click();
  await page.getByRole("heading", { name: "运行总览", exact: true }).waitFor();
  await page.getByText("模型配置暂不可用", { exact: true }).waitFor();
  if (!await page.getByLabel("配置文本对话").isDisabled()) throw new Error("Configuration button should be disabled while the backend route is unavailable.");
  configurationAvailable = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByLabel("配置文本对话").waitFor({ state: "visible" });
  if (await page.getByLabel("配置文本对话").isDisabled()) throw new Error("Configuration button did not recover after the backend route became available.");
  await page.getByLabel("配置文本对话").click();
  await page.locator(".ant-drawer-title", { hasText: "配置文本对话" }).waitFor();
  await page.screenshot({ path: path.join(outputDir, "desktop-admin-model-config.png"), fullPage: true });
  await page.getByRole("button", { name: "Close" }).click();

  // Speech has one entry point, and saving the model leads to voice configuration.
  await expect(page.getByRole("navigation").getByRole("button", { name: "声音设置", exact: true })).toHaveCount(0);
  await page.getByLabel("配置云端语音合成").click();
  await page.getByRole("button", { name: "保存并生效", exact: true }).click();
  await expect(page.getByRole("tab", { name: "音色与播报" })).toHaveAttribute("aria-selected", "true");
  expect(savedModel).not.toHaveProperty("api_key");
  expect(savedModel).not.toHaveProperty("voice");
  await page.getByLabel("云端音色 ID", { exact: true }).fill("tts-model:test-voice");
  await page.getByRole("button", { name: "保存播报设置" }).click();
  await page.getByText("声音设置已保存，患者端刷新后生效。").waitFor();
  expect(speech.cloud_voice).toBe("tts-model:test-voice");
  await page.getByRole("button", { name: "试听", exact: true }).click();
  await expect(page.getByText("供应商说明：Invalid voice.", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(outputDir, "desktop-admin-unified-speech.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "保存播报设置" })).toBeVisible();
  await page.screenshot({ path: path.join(outputDir, "mobile-admin-unified-speech.png"), fullPage: true });
  await page.getByRole("button", { name: "Close" }).click();
  await page.setViewportSize({ width: 1440, height: 960 });

  await page.getByRole("button", { name: "检索与评测", exact: true }).click();
  await page.getByLabel("测试问题").fill("术后多久可以侧睡");
  await page.getByRole("button", { name: "测试检索" }).click();
  await page.getByRole("button", { name: /查看详情/ }).click();
  await page.locator(".ant-drawer-title", { hasText: "检索命中 #1" }).waitFor();
  await page.screenshot({ path: path.join(outputDir, "desktop-admin-retrieval-drawer.png"), fullPage: true });
  await page.getByRole("button", { name: "Close" }).click();

  const turnInputs = page.locator(".conversation-test-turn textarea");
  await turnInputs.nth(0).fill("孩子术后多久可以侧睡？");
  await turnInputs.nth(1).fill("如果睡着后不小心碰到了呢？");
  await page.getByRole("button", { name: "运行多轮测试" }).click();
  await page.getByRole("row", { name: /第 2 轮/ }).getByRole("button", { name: "查看" }).click();
  await page.locator(".ant-drawer-title", { hasText: "第 2 轮对话详情" }).waitFor();
  await page.screenshot({ path: path.join(outputDir, "desktop-admin-conversation-test.png"), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  if (overflow) throw new Error("Admin UI has horizontal overflow.");
  await context.close();
  console.log("Admin model configuration and right-side detail drawers passed.");
} finally {
  await browser.close();
}
