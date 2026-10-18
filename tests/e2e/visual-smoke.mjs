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
const pixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  ...(executablePath ? { executablePath } : {}),
  args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
});

try {
  for (const target of [
    { name: "desktop", width: 1440, height: 900 },
    { name: "mobile", width: 390, height: 844 },
  ]) {
    const context = await browser.newContext({
      viewport: { width: target.width, height: target.height },
      deviceScaleFactor: 1,
      ignoreHTTPSErrors: true,
      permissions: ["camera"],
    });
    const page = await context.newPage();
    const uploadedMediaIds = [];
    if (target.name === "mobile") {
      await page.route("**/api/v1/conversations/*/messages/stream", async (route) => {
        const conversationId = route.request().url().split("/").at(-3);
        const requestBody = route.request().postDataJSON();
        const attachmentIds = requestBody.attachment_ids ?? [];
        uploadedMediaIds.push(...attachmentIds);
        const content =
          requestBody.content || "请对上传图片中与耳部健康科普相关的可见信息进行客观说明。";
        const createdAt = new Date().toISOString();
        const userMessage = {
          id: "visual-user-message",
          conversation_id: conversationId,
          sequence_no: 1,
          role: "user",
          content,
          status: "completed",
          safety_code: null,
          created_at: createdAt,
          attachments: attachmentIds.map((id, index) => ({
            id,
            kind: "image",
            original_name: `visual-${index + 1}.png`,
            media_type: "image/png",
            size_bytes: 68,
            status: "attached",
            created_at: createdAt,
          })),
        };
        const assistantMessage = {
          id: "visual-assistant-message",
          conversation_id: conversationId,
          sequence_no: 2,
          role: "assistant",
          content: "就诊前可以整理既往检查资料和想咨询的问题。具体情况请由医生面诊评估。",
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
    }
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
    const mediaButton = page.getByRole("button", { name: "添加图片", exact: true });
    const voiceButton = page.getByRole("button", { name: "切换到按住说话", exact: true });
    if (!(await mediaButton.isVisible()) || !(await voiceButton.isVisible())) {
      throw new Error(`${target.name} compact composer actions are not visible`);
    }
    if (await voiceButton.isDisabled()) {
      throw new Error(`${target.name} voice mode switch is unexpectedly disabled`);
    }
    if ((await page.locator("input.image-input").getAttribute("multiple")) === null) {
      throw new Error(`${target.name} unified image input does not support multiple files`);
    }
    const desktopCamera = page.getByRole("button", { name: "使用电脑摄像头拍照" });
    if (target.name === "desktop") {
      if (!(await desktopCamera.isVisible())) throw new Error("desktop camera button is not visible");
      await desktopCamera.click();
      await page.getByRole("dialog", { name: "电脑拍照" }).waitFor();
      await page.waitForTimeout(1500);
      await page.screenshot({ path: path.join(outputDir, "desktop-camera-playwright.png") });
      const cameraErrorVisible = await page.locator(".camera-error").isVisible().catch(() => false);
      const captureReady = await page
        .getByRole("button", { name: "拍摄", exact: true })
        .isEnabled()
        .catch(() => false);
      if (cameraErrorVisible || !captureReady) {
        await page.getByRole("button", { name: "关闭相机" }).click();
      } else {
        await page.getByRole("button", { name: "拍摄", exact: true }).click();
        await page.locator(".selected-image").waitFor();
        await page.locator(".selected-image button").click();
      }
    } else if (await desktopCamera.isVisible()) {
      throw new Error("desktop camera button should be hidden on mobile");
    }

    await page.screenshot({ path: path.join(outputDir, `${target.name}-playwright.png`) });

    const [imageChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      mediaButton.click(),
    ]);
    if (!imageChooser.isMultiple()) {
      throw new Error(`${target.name} unified image chooser is not multiple`);
    }
    await imageChooser.setFiles({
      name: "gallery-photo.png",
      mimeType: "image/png",
      buffer: pixelPng,
    });
    await page.locator(".selected-image").waitFor();
    await page.locator(".selected-image button").click();

    if (target.name === "desktop") {
      const collapseButton = page.getByRole("button", { name: "收起侧边栏" });
      await collapseButton.click();
      await page.locator(".app-shell.is-sidebar-collapsed").waitFor();
      await page.waitForTimeout(250);
      const collapsedWidth = await page.locator(".history-sidebar").evaluate(
        (element) => element.getBoundingClientRect().width,
      );
      if (collapsedWidth > 80) {
        throw new Error(`desktop collapsed sidebar is too wide: ${collapsedWidth}`);
      }
      await page.screenshot({ path: path.join(outputDir, "desktop-sidebar-collapsed.png") });
      await page.reload({ waitUntil: "networkidle" });
      await page.locator(".app-shell.is-sidebar-collapsed").waitFor();
      await page.getByRole("button", { name: "展开侧边栏" }).click();
      await page.locator(".app-shell.is-sidebar-collapsed").waitFor({ state: "detached" });
      const sampleConversationIds = await page.evaluate(async () => {
        const visitorId = localStorage.getItem("medical-agent-visitor-id");
        if (!visitorId) throw new Error("Missing visitor ID for sidebar check.");
        const headers = { "Content-Type": "application/json", "X-Visitor-ID": visitorId };
        const titles = [
          "术前需要准备哪些检查资料？",
          "孩子耳廓形态与日常护理注意事项",
          "第一次门诊咨询",
        ];
        return Promise.all(
          titles.map(async (title) => {
            const response = await fetch("/api/v1/conversations", {
              method: "POST",
              headers,
              body: JSON.stringify({ title }),
            });
            return (await response.json()).id;
          }),
        );
      });
      await page.reload({ waitUntil: "networkidle" });
      await page.locator(".history-select", { hasText: "孩子耳廓形态与日常护理注意事项" }).click();
      await page.screenshot({ path: path.join(outputDir, "desktop-history-items.png") });
      await page.evaluate(async (conversationIds) => {
        const visitorId = localStorage.getItem("medical-agent-visitor-id");
        if (!visitorId) return;
        const headers = { "X-Visitor-ID": visitorId };
        await Promise.all(
          conversationIds.map((conversationId) =>
            fetch(`/api/v1/conversations/${conversationId}`, { method: "DELETE", headers }),
          ),
        );
      }, sampleConversationIds);
    }

    if (target.name === "mobile") {
      const composerCenters = await page.evaluate(() =>
        [
          document.querySelector(".media-picker"),
          document.querySelector(".composer textarea"),
          document.querySelector(".composer-mode-switch"),
          document.querySelector(".send-button"),
        ].map((element) => {
          const bounds = element?.getBoundingClientRect();
          return bounds ? bounds.top + bounds.height / 2 : null;
        }),
      );
      if (
        composerCenters.some((center) => center === null) ||
        Math.max(...composerCenters) - Math.min(...composerCenters) > 2
      ) {
        throw new Error(`mobile composer controls are not aligned: ${composerCenters}`);
      }
      await page.locator("input.image-input").setInputFiles([
        { name: "visual-front.png", mimeType: "image/png", buffer: pixelPng },
        { name: "visual-side.png", mimeType: "image/png", buffer: pixelPng },
      ]);
      await page.locator(".selected-image").nth(1).waitFor();
      const composerLayout = await page.evaluate(() => ({
        viewportWidth: window.innerWidth,
        pageWidth: document.documentElement.scrollWidth,
      }));
      if (composerLayout.pageWidth > composerLayout.viewportWidth) {
        throw new Error(`mobile composer has horizontal overflow: ${JSON.stringify(composerLayout)}`);
      }
      await page.screenshot({
        path: path.join(outputDir, "mobile-multimodal-composer-playwright.png"),
      });
      await sendButton.click();
      await page.locator(".message-row.assistant .message-body").waitFor({
        timeout: 10000,
      });
      await page.locator(".message-images img").nth(1).waitFor();
      const loadedImages = await page.locator(".message-images img").evaluateAll((images) =>
        images.map((image) => ({
          complete: image.complete,
          naturalWidth: image.naturalWidth,
        })),
      );
      if (loadedImages.some((image) => !image.complete || image.naturalWidth === 0)) {
        throw new Error(`mobile message images failed to load: ${JSON.stringify(loadedImages)}`);
      }
      const messageLayout = await page.evaluate(() => {
        const bubble = document.querySelector(".message-content.has-attachments");
        const bounds = bubble?.getBoundingClientRect();
        return {
          viewportWidth: window.innerWidth,
          pageWidth: document.documentElement.scrollWidth,
          bubbleRight: bounds?.right ?? 0,
        };
      });
      if (
        messageLayout.pageWidth > messageLayout.viewportWidth ||
        messageLayout.bubbleRight > messageLayout.viewportWidth
      ) {
        throw new Error(`mobile image message overflows: ${JSON.stringify(messageLayout)}`);
      }
      await page.screenshot({ path: path.join(outputDir, "mobile-message-playwright.png") });

      await page.locator(".history-trigger").click();
      await page.locator(".history-sidebar.is-open").waitFor();
      await page.screenshot({ path: path.join(outputDir, "mobile-history-playwright.png") });

      await page.evaluate(async (mediaIds) => {
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
        await Promise.all(
          mediaIds.map((mediaId) =>
            fetch(`/api/v1/media/${mediaId}`, {
              method: "DELETE",
              headers,
            }),
          ),
        );
      }, uploadedMediaIds);
    }
    await context.close();
  }
  console.log("Visual smoke check passed for desktop and mobile viewports.");
} finally {
  await browser.close();
}
