import { existsSync } from "node:fs";

import { chromium } from "@playwright/test";

const localProtocol = existsSync(".certs/dev-server.crt") ? "https" : "http";
const baseUrl = process.env.E2E_BASE_URL ?? `${localProtocol}://127.0.0.1:5173`;
const apiBaseUrl = process.env.E2E_API_BASE_URL ?? "/api/v1";
const browserCandidates = [
  process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean);
const executablePath = browserCandidates.find((candidate) => existsSync(candidate));
const browser = await chromium.launch(executablePath ? { executablePath } : {});

try {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "第一次就诊前需要准备什么？" }).click();
  await page.getByRole("button", { name: "发送问题" }).click();

  const stopButton = page.getByRole("button", { name: "停止生成" });
  await stopButton.waitFor();
  const streamingAnswer = page.locator(".message-row.assistant .is-streaming");
  const streamingElement = await streamingAnswer.elementHandle({ timeout: 10000 });
  const partialText = await streamingElement?.textContent();
  if (!partialText?.trim()) throw new Error("No partial answer was rendered before completion.");

  await stopButton.click();
  await page.getByText("回答未完整生成").waitFor();
  await page.getByRole("button", { name: "发送问题" }).waitFor();

  const persistedStatus = await page.evaluate(async (apiBase) => {
    const visitorId = localStorage.getItem("medical-agent-visitor-id");
    if (!visitorId) throw new Error("Missing visitor ID.");
    const headers = { "X-Visitor-ID": visitorId };
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const list = await (await fetch(`${apiBase}/conversations`, { headers })).json();
      const detail = await (
        await fetch(`${apiBase}/conversations/${list[0].id}`, { headers })
      ).json();
      const assistant = detail.messages.findLast((message) => message.role === "assistant");
      if (assistant) {
        await fetch(`${apiBase}/conversations/${list[0].id}`, { method: "DELETE", headers });
        return assistant.status;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return null;
  }, apiBaseUrl);
  if (persistedStatus !== "interrupted") {
    throw new Error(`Expected interrupted history message, received: ${persistedStatus}`);
  }

  await context.close();
  console.log("Streaming stop and partial-history persistence check passed.");
} finally {
  await browser.close();
}
