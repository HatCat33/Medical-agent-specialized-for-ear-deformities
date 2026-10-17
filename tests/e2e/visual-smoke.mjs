import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
const outputDir = path.resolve(".artifacts", "visual-check");
const browserCandidates = [
  process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean);
const executablePath = browserCandidates.find((candidate) => existsSync(candidate));

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch(executablePath ? { executablePath } : {});

try {
  for (const target of [
    { name: "desktop", width: 1440, height: 900 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport: { width: target.width, height: target.height },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    await page.goto(baseUrl, { waitUntil: "networkidle" });

    const layout = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      pageWidth: document.documentElement.scrollWidth,
    }));
    if (layout.pageWidth > layout.viewportWidth) {
      throw new Error(`${target.name} has horizontal overflow: ${JSON.stringify(layout)}`);
    }

    const sendButton = page.getByRole("button", { name: "发送问题" });
    if (!(await sendButton.isVisible())) {
      throw new Error(`${target.name} send button is not visible`);
    }

    await page.screenshot({ path: path.join(outputDir, `${target.name}-playwright.png`) });

    if (target.name === "mobile") {
      await page.getByRole("button", { name: "第一次就诊前需要准备什么？" }).click();
      await sendButton.click();
      await page.getByRole("button", { name: "停止生成" }).waitFor();
      await page.locator(".message-row.assistant .is-streaming").waitFor({
        timeout: 10000,
      });
      await page.getByRole("button", { name: "停止生成" }).waitFor({
        state: "hidden",
        timeout: 75000,
      });
      await page.screenshot({ path: path.join(outputDir, "mobile-message-playwright.png") });

      await page.locator(".history-trigger").click();
      await page.locator(".history-sidebar.is-open").waitFor();
      await page.screenshot({ path: path.join(outputDir, "mobile-history-playwright.png") });

      await page.evaluate(async () => {
        const visitorId = localStorage.getItem("medical-agent-visitor-id");
        if (!visitorId) return;
        const headers = { "X-Visitor-ID": visitorId };
        const response = await fetch("/api/v1/conversations", { headers });
        const conversations = await response.json();
        await Promise.all(
          conversations.map((conversation) =>
            fetch(`/api/v1/conversations/${conversation.id}`, {
              method: "DELETE",
              headers,
            }),
          ),
        );
      });
    }
    await context.close();
  }
  console.log("Visual smoke check passed for desktop and mobile viewports.");
} finally {
  await browser.close();
}
