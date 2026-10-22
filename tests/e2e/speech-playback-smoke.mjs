import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "https://127.0.0.1:5183";
const executablePath = [process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean).find(file => existsSync(file));
const output = path.resolve(".artifacts/visual-check");
await mkdir(output, { recursive: true });
const browser = await chromium.launch(executablePath ? { executablePath } : {});
const timestamp = new Date().toISOString();
const conversation = { id: "speech-test", title: "声音播报验收", created_at: timestamp, updated_at: timestamp, last_message_at: timestamp };
function message(id, role, content, sequence) {
  return { id, role, content, sequence_no: sequence, conversation_id: conversation.id,
    status: "completed", safety_code: null, created_at: timestamp, attachments: [] };
}

try {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const device = viewport.width > 700 ? "desktop" : "mobile";
    const context = await browser.newContext({ viewport, ignoreHTTPSErrors: true });
    await context.addInitScript(() => {
      window.__spoken = []; window.__cancels = 0;
      Object.defineProperty(window, "SpeechSynthesisUtterance", { configurable: true, value: class { constructor(text) { this.text = text; } } });
      Object.defineProperty(window, "speechSynthesis", { configurable: true, value: {
        getVoices: () => [{ name: "测试中文", voiceURI: "test-chinese", lang: "zh-CN" }],
        resume() {}, cancel() { window.__cancels += 1; },
        speak(utterance) {
          window.__spoken.push({ text: utterance.text, rate: utterance.rate, voice: utterance.voice?.voiceURI });
          setTimeout(() => utterance.onend?.(), 30);
        },
      } });
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/v1/speech/settings", route => route.fulfill({ json: {
      auto_play: true, voice: "test-chinese", language: "zh-CN", rate: 1.25, pitch: 1, volume: 0.8,
    } }));
    await page.route("**/api/v1/conversations", route => route.fulfill({ json: [conversation] }));
    await page.route(`**/api/v1/conversations/${conversation.id}`, route => route.fulfill({ json: {
      ...conversation, messages: [message("old", "assistant", "历史回答不应自动朗读。", 1)],
    } }));
    let sequence = 2;
    await page.route("**/api/v1/conversations/*/messages/stream", route => {
      const content = route.request().postDataJSON().content;
      const user = message(`user-${sequence}`, "user", content, sequence++);
      const assistant = message(`assistant-${sequence}`, "assistant", "**第一句科普回答**。第二句回答。\n\n参考资料：\n- 内部资料", sequence++);
      return route.fulfill({ contentType: "text/event-stream", body: [
        `event: user_message\ndata: ${JSON.stringify({ message: user })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ content: "**第一句科普回答**。" })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ content: "第二句回答。\n\n参考资料：\n- 内部资料" })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ message: assistant })}\n\n`,
      ].join("") });
    });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    if (device === "mobile") {
      await page.evaluate(() => {
        const touch = (x, y) => new Touch({ identifier: 1, target: document.body, clientX: x, clientY: y, pageX: x, pageY: y, screenX: x, screenY: y, radiusX: 1, radiusY: 1, rotationAngle: 0, force: 1 });
        document.dispatchEvent(new TouchEvent("touchstart", { touches: [touch(140, 180)], changedTouches: [touch(140, 180)], bubbles: true }));
        document.dispatchEvent(new TouchEvent("touchmove", { touches: [touch(390, 184)], changedTouches: [touch(390, 184)], bubbles: true, cancelable: true }));
      });
      const dragShadeOpacity = await page.getByRole("button", { name: "返回对话" }).evaluate(element => Number(getComputedStyle(element).opacity));
      expect(dragShadeOpacity).toBeGreaterThan(0.75);
      expect(dragShadeOpacity).toBeLessThan(0.85);
      await page.evaluate(() => {
        const touch = new Touch({ identifier: 1, target: document.body, clientX: 390, clientY: 184, pageX: 390, pageY: 184, screenX: 390, screenY: 184, radiusX: 1, radiusY: 1, rotationAngle: 0, force: 1 });
        document.dispatchEvent(new TouchEvent("touchend", { touches: [], changedTouches: [touch], bubbles: true }));
      });
      const releaseShadeOpacity = await page.getByRole("button", { name: "返回对话" }).evaluate(element => Number(getComputedStyle(element).opacity));
      expect(Math.abs(releaseShadeOpacity - dragShadeOpacity)).toBeLessThan(0.08);
      await expect(page.locator(".history-sidebar")).toHaveClass(/is-open/);
      await expect(page.locator(".sidebar-backdrop")).toHaveCount(0);
      expect(await page.locator(".chat-shell").evaluate(element => getComputedStyle(element).transform)).not.toBe("none");
      await expect(page.getByRole("button", { name: "返回对话" })).toBeVisible();
      await page.getByRole("button", { name: "返回对话" }).click();
      await expect(page.locator(".history-sidebar")).not.toHaveClass(/is-open/);
      await page.getByRole("button", { name: "打开历史会话" }).click();
      await expect(page.locator(".history-sidebar")).toHaveClass(/is-open/);
      await page.evaluate(() => {
        const touch = (x, y) => new Touch({ identifier: 1, target: document.body, clientX: x, clientY: y, pageX: x, pageY: y, screenX: x, screenY: y, radiusX: 1, radiusY: 1, rotationAngle: 0, force: 1 });
        document.dispatchEvent(new TouchEvent("touchstart", { touches: [touch(150, 180)], changedTouches: [touch(150, 180)], bubbles: true }));
        document.dispatchEvent(new TouchEvent("touchmove", { touches: [touch(30, 184)], changedTouches: [touch(30, 184)], bubbles: true, cancelable: true }));
        document.dispatchEvent(new TouchEvent("touchend", { touches: [], changedTouches: [touch(0, 184)], bubbles: true }));
      });
      await expect(page.locator(".history-sidebar")).not.toHaveClass(/is-open/);
    }
    await expect(page.getByText("历史回答不应自动朗读。")).toBeVisible();
    expect(await page.evaluate(() => window.__spoken.length)).toBe(0);
    const input = page.locator("textarea");
    async function send(text) {
      await input.fill(text);
      await page.getByRole("button", { name: "发送问题", exact: true }).click();
      await expect(page.getByRole("button", { name: "停止生成", exact: true })).toHaveCount(0);
    }
    await send("请介绍就诊准备");
    await page.waitForFunction(() => window.__spoken.length === 2);
    expect(await page.evaluate(() => window.__spoken)).toEqual([
      { text: "第一句科普回答。", rate: 1.25, voice: "test-chinese" },
      { text: "第二句回答。", rate: 1.25, voice: "test-chinese" },
    ]);
    await expect(page.getByRole("button", { name: "播放回答", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "关闭自动播报", exact: true }).click();
    await send("请继续说明");
    expect(await page.evaluate(() => window.__spoken.length)).toBe(2);
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.getByRole("button", { name: "开启自动播报", exact: true })).toBeVisible();
    expect(await page.evaluate(() => window.__spoken.length)).toBe(0);
    await page.getByRole("button", { name: "开启自动播报", exact: true }).click();
    await send("请再介绍一下");
    await page.waitForFunction(() => window.__spoken.length === 2);
    const brand = await page.locator(".new-conversation-trigger").boundingBox();
    const history = await page.locator(".history-trigger").boundingBox();
    const heading = await page.locator(".chat-heading").boundingBox();
    const toggle = await page.locator(".auto-speech-toggle").boundingBox();
    if (!brand || !heading || !toggle || brand.x + brand.width > heading.x || heading.x + heading.width > toggle.x) throw new Error(`${device}: header overlap`);
    if (device === "desktop" && history) throw new Error("desktop: history trigger should be hidden");
    if (device === "mobile" && !history) throw new Error("mobile: history trigger should be visible");
    if (await page.locator(".brand-mark").count()) throw new Error(`${device}: old header icon should be removed`);
    if (await page.locator(".stream-cursor").count()) throw new Error(`${device}: streaming cursor should not render`);
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`${device}: horizontal overflow`);
    await page.screenshot({ path: path.join(output, `${device}-automatic-speech.png`), fullPage: true });
    expect(errors).toEqual([]);
    await context.close();
  }
  console.log("Patient speech passed on desktop/mobile: auto playback, mute, preference persistence, voice settings, history silence, header layout.");
} finally { await browser.close(); }
