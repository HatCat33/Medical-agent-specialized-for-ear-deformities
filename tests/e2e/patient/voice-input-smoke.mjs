import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";

const localProtocol = existsSync(".certs/dev-server.crt") ? "https" : "http";
const baseUrl = process.env.E2E_BASE_URL ?? `${localProtocol}://127.0.0.1:5173`;
const outputDir = path.resolve(".artifacts", "visual-check");
const browserCandidates = [
  process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean);
const executablePath = browserCandidates.find((candidate) => existsSync(candidate));

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
});

try {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    ignoreHTTPSErrors: true,
    permissions: ["microphone"],
  });
  const page = await context.newPage();
  let transcriptionRequests = 0;
  let streamRequests = 0;

  await page.route("**/api/v1/speech/transcriptions", async (route) => {
    const request = route.request();
    const contentType = request.headers()["content-type"] ?? "";
    const body = request.postDataBuffer();
    if (!contentType.includes("multipart/form-data") || !body || body.length < 128) {
      throw new Error("Speech request did not contain recorded multipart audio.");
    }
    transcriptionRequests += 1;
    await page.waitForTimeout(600);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ text: "语音输入测试问题" }),
    });
  });

  await page.route("**/api/v1/conversations/*/messages/stream", async (route) => {
    const requestBody = route.request().postDataJSON();
    if (requestBody.content !== "语音输入测试问题" || requestBody.input_mode !== "voice") {
      throw new Error(`Unexpected voice message payload: ${JSON.stringify(requestBody)}`);
    }
    streamRequests += 1;
    const conversationId = route.request().url().split("/").at(-3);
    const createdAt = new Date().toISOString();
    const userMessage = {
      id: "voice-user-message",
      conversation_id: conversationId,
      sequence_no: 1,
      role: "user",
      content: requestBody.content,
      status: "completed",
      safety_code: null,
      created_at: createdAt,
      attachments: [],
    };
    const assistantMessage = {
      id: "voice-assistant-message",
      conversation_id: conversationId,
      sequence_no: 2,
      role: "assistant",
      content: "已收到你的语音问题。",
      status: "completed",
      safety_code: "allowed",
      created_at: createdAt,
      attachments: [],
    };
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        `event: user_message\ndata: ${JSON.stringify({ message: userMessage })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ content: assistantMessage.content })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ message: assistantMessage })}\n\n`,
      ].join(""),
    });
  });

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "切换到按住说话" }).click();
  const holdButton = page.getByRole("button", { name: "按住说话，松开发送" });
  await holdButton.waitFor();
  await page.screenshot({ path: path.join(outputDir, "mobile-voice-ready-playwright.png") });

  const bounds = await holdButton.boundingBox();
  if (!bounds) throw new Error("Hold-to-talk button has no layout bounds.");
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(350);
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2 - 70);
  await page.getByText("松开取消", { exact: true }).waitFor();
  await page.screenshot({ path: path.join(outputDir, "mobile-voice-cancel-playwright.png") });
  await page.mouse.up();
  await page.waitForTimeout(300);
  if (transcriptionRequests !== 0) {
    throw new Error("Cancelled recording was unexpectedly sent for transcription.");
  }

  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(outputDir, "mobile-voice-recording-playwright.png") });
  await page.mouse.up();

  await page.getByText("正在识别语音", { exact: true }).waitFor();
  await page.screenshot({ path: path.join(outputDir, "mobile-voice-recognizing-playwright.png") });
  await page.locator(".message-row.user", { hasText: "语音输入测试问题" }).waitFor({
    timeout: 15000,
  });
  await page.getByText("已收到你的语音问题。").waitFor();
  if (transcriptionRequests !== 1 || streamRequests !== 1) {
    throw new Error(
      `Unexpected request counts: transcription=${transcriptionRequests}, stream=${streamRequests}`,
    );
  }

  await page.screenshot({ path: path.join(outputDir, "mobile-voice-sent-playwright.png") });
  await page.evaluate(async () => {
    const visitorId = localStorage.getItem("medical-agent-visitor-id");
    if (!visitorId) return;
    const headers = { "X-Visitor-ID": visitorId };
    const response = await fetch("/api/v1/conversations", { headers });
    const conversations = await response.json();
    await Promise.all(
      conversations.map((conversation) =>
        fetch(`/api/v1/conversations/${conversation.id}`, { method: "DELETE", headers }),
      ),
    );
  });
  await context.close();
  console.log("Hold-to-talk transcription and automatic send check passed.");
} finally {
  await browser.close();
}
