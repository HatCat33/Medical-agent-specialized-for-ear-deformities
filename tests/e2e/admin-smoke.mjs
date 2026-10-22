// Run against the isolated admin fixture servers documented in docs/testing/README.md.
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.ADMIN_E2E_BASE_URL ?? "http://127.0.0.1:5184";
const apiUrl = process.env.ADMIN_E2E_API_URL ?? "http://127.0.0.1:8011/api/v1";
const token = "e2e-admin-only";
const check = await fetch(`${apiUrl}/admin/knowledge/overview`, { headers: { "X-Admin-Token": token } });
if (!check.ok) throw new Error("Start the isolated E2E backend first.");
const overview = await check.json();
if (overview.database !== "sqlite" || overview.embedding_configured) throw new Error("E2E requires isolated SQLite with Embedding disabled.");
const executablePath = [process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean).find(file => existsSync(file));
const output = path.resolve(".artifacts/visual-check");
await mkdir(output, { recursive: true });
const browser = await chromium.launch(executablePath ? { executablePath } : {});
try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const device = viewport.width > 700 ? "desktop" : "mobile";
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(baseUrl);
    await page.getByLabel("管理员令牌").fill(token);
    await page.getByRole("button", { name: "进入管理端" }).click();
    await page.getByRole("heading", { name: "运行总览", exact: true }).waitFor();
    await expect(page.locator("aside")).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "管理功能" })).toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: "文本对话" })).toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: "图片理解" })).toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: "语音识别" })).toBeVisible();
    for (const name of ["云端语音合成", "设备语音"]) {
      await expect(page.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "探活", exact: true })).toBeEnabled();
    }
    await page.screenshot({ path: path.join(output, `${device}-admin-overview.png`), fullPage: true });
    if (device === "desktop") {
      const brand = await page.locator(".admin-brand").boundingBox();
      const navigation = await page.getByRole("navigation", { name: "管理功能" }).boundingBox();
      if (!brand || !navigation || Math.abs(brand.y - navigation.y) > 20) throw new Error("Desktop header must be a single unified row.");
    }
    await page.getByRole("button", { name: "提示词管理", exact: true }).click();
    await page.getByRole("heading", { name: "提示词版本", exact: true }).waitFor();
    const createPromptButton = page.locator("button").filter({ hasText: "基于当前版本创建草稿" });
    await createPromptButton.click();
    await expect(createPromptButton).toBeEnabled();
    const promptName = `提示词验收 ${device} ${Date.now()}`;
    await page.getByLabel("版本名称", { exact: true }).fill(promptName);
    await page.getByLabel("提示词内容", { exact: true }).fill("使用简洁中文回答患者问题。这是隔离环境验收提示词。");
    await page.getByLabel("系统提示词内容", { exact: true }).fill("你是测试科普助手。不进行诊断。这是隔离环境系统提示词。");
    await page.locator("button").filter({ hasText: "保存草稿" }).click();
    const promptRow = page.getByRole("row").filter({ hasText: promptName });
    await promptRow.getByRole("button", { name: "发布", exact: true }).click();
    await page.getByText("发布这个提示词版本？", { exact: true }).waitFor();
    await page.getByRole("button", { name: /确认发布/ }).click();
    await page.getByText(`当前已发布：${promptName}`).waitFor();
    await page.screenshot({ path: path.join(output, `${device}-admin-prompts.png`), fullPage: true });
    await page.getByRole("button", { name: "运行总览", exact: true }).click();
    const chatModelRow = page.getByRole("row").filter({ hasText: "文本对话" });
    const chatProbe = page.waitForResponse(response => response.url().endsWith("/admin/system/models/chat/probe") && response.status() === 200);
    await chatModelRow.getByRole("button", { name: "探活", exact: true }).click();
    await chatProbe;
    await expect(chatModelRow.getByRole("button", { name: "探活", exact: true })).toBeEnabled();
    const embeddingRow = page.getByRole("row").filter({ hasText: "文本向量" });
    await embeddingRow.getByRole("button", { name: "探活", exact: true }).click();
    await page.getByText("模型配置不完整", { exact: true }).waitFor();
    await page.getByText(/补全 EMBEDDING_BASE_URL/).waitFor();
    await page.screenshot({ path: path.join(output, `${device}-admin-models.png`), fullPage: true });
    await page.getByRole("button", { name: "声音设置", exact: true }).click();
    const saveColor = await page.getByRole("button", { name: "保存设置", exact: true }).locator("span").last().evaluate(element => getComputedStyle(element).color);
    if (saveColor !== "rgb(255, 255, 255)") throw new Error(`Save button text lacks contrast: ${saveColor}`);
    await page.getByLabel("音色名称", { exact: true }).fill("e2e-device-voice");
    await page.locator("button").filter({ hasText: "保存设置" }).click();
    await page.getByText("声音设置已保存，患者端刷新后生效。").waitFor();
    const speech = await fetch(`${apiUrl}/speech/settings`).then(response => response.json());
    if (speech.voice !== "e2e-device-voice") throw new Error("Speech defaults did not persist.");
    await page.screenshot({ path: path.join(output, `${device}-admin-speech.png`), fullPage: true });
    await page.locator(".speech-provider-card .ant-select-selector").click();
    await page.getByText("云端语音 · 自定义音色", { exact: true }).last().click();
    await page.getByLabel("云端音色 ID", { exact: true }).fill("cloned:test-voice");
    await page.screenshot({ path: path.join(output, `${device}-admin-cloud-voice.png`), fullPage: true });
    await page.getByRole("button", { name: "知识版本", exact: true }).click();
    const releaseName = `验收 ${device} ${Date.now()}`;
    await page.getByLabel("新版本名称").fill(releaseName);
    await page.getByRole("button", { name: "新建空白草稿" }).click();
    await page.getByText("草稿已创建，可进入分块管理编辑内容。").waitFor();
    await page.getByRole("heading", { name: releaseName, exact: true }).waitFor();
    await page.getByRole("button", { name: "分块管理", exact: true }).click();
    await page.getByRole("button", { name: "新增条目" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("分类", { exact: true }).fill("就诊准备");
    await dialog.getByLabel("标准问题", { exact: true }).fill("初诊需要带什么资料");
    await dialog.getByLabel("答案内容", { exact: true }).fill("请携带既往检查资料。此为验收测试内容。");
    await dialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "初诊需要带什么资料", exact: true }).click();
    await page.getByRole("heading", { name: "实际向量化文本" }).waitFor();
    await page.screenshot({ path: path.join(output, `${device}-admin-chunk-detail.png`), fullPage: true });
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page.getByLabel("答案内容", { exact: true }).fill("携带身份证与既往检查资料。此为修改后的验收内容。");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page.locator(".ant-spin-spinning")).toHaveCount(0);
    await page.screenshot({ path: path.join(output, `${device}-admin-chunks.png`), fullPage: true });
    await page.getByRole("button", { name: "检索与评测", exact: true }).click();
    await page.getByLabel("测试问题").fill("初诊需要带什么资料");
    await page.getByRole("button", { name: "测试检索", exact: true }).click();
    await page.getByText(/条命中/).waitFor();
    await page.locator("button").filter({ hasText: "从知识库自动生成" }).click();
    await page.getByText(/已生成 1 条/).waitFor();
    await page.screenshot({ path: path.join(output, `${device}-admin-evaluation-before-run.png`), fullPage: true });
    await page.getByRole("button", { name: /运行评测.*1/ }).click();
    await page.getByRole("button", { name: "导出评测报告", exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, `${device}-admin-evaluation.png`), fullPage: true });
    await page.getByRole("button", { name: "任务与记录", exact: true }).click();
    await page.getByRole("cell", { name: "检索评测", exact: true }).first().waitFor();
    await page.screenshot({ path: path.join(output, `${device}-admin-operations.png`), fullPage: true });
    await page.getByRole("tab", { name: "系统操作审计", exact: true }).click();
    await page.getByRole("cell", { name: "修改声音设置", exact: true }).first().waitFor();
    await page.getByRole("button", { name: "知识版本", exact: true }).click();
    await page.getByRole("button", { name: "发布版本", exact: true }).click();
    await page.getByRole("button", { name: "确认", exact: true }).click();
    await page.getByText("版本已发布，后续患者问答使用此版本。").waitFor();
    await page.getByRole("button", { name: "分块管理", exact: true }).click();
    await expect(page.getByRole("button", { name: "编辑", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "知识版本", exact: true }).click();
    await page.getByRole("button", { name: "复制当前版本为草稿", exact: true }).click();
    await page.getByText("草稿已创建，可进入分块管理编辑内容。").waitFor();
    await page.getByRole("button", { name: "分块管理", exact: true }).click();
    await expect(page.getByRole("button", { name: "编辑", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "删除", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "删除", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await expect(page.getByRole("button", { name: "初诊需要带什么资料", exact: true })).toHaveCount(0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow || errors.length) throw new Error(`${device}: overflow=${overflow}, errors=${errors.join(",")}`);
    await context.close();
  }
  console.log("Admin real API smoke passed: top navigation, prompts, models, CRUD, publish, retrieval, evaluation, audit.");
} finally { await browser.close(); }
