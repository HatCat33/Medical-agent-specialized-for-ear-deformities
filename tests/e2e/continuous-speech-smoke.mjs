import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "https://127.0.0.1:5173";
const executablePath = [process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].filter(Boolean).find(existsSync);
// Real, decodable two-second silent WAV. No cloud fees or patient data.
const audio = Buffer.alloc(44 + 16000 * 2 * 2);
audio.write("RIFF"); audio.writeUInt32LE(audio.length - 8, 4); audio.write("WAVEfmt ", 8);
audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22);
audio.writeUInt32LE(16000, 24); audio.writeUInt32LE(32000, 28);
audio.writeUInt16LE(2, 32); audio.writeUInt16LE(16, 34);
audio.write("data", 36); audio.writeUInt32LE(audio.length - 44, 40);
const now = new Date().toISOString();
const conversation = { id: "continuous-speech", title: "连续播报测试", created_at: now, updated_at: now, last_message_at: now };
const answer = "这是一段用于连续播报测试的科普说明，请按医院建议准备资料并咨询专业医生。".repeat(22);
const message = (id, role, content, sequence_no) => ({ id, role, content, sequence_no,
  conversation_id: conversation.id, created_at: now, status: "completed", attachments: [] });
await mkdir(".artifacts/visual-check", { recursive: true });
const browser = await chromium.launch(executablePath ? { executablePath } : {});
try {
  for (const mobile of [false, true]) {
    const context = await browser.newContext({ viewport: { width: mobile ? 390 : 1440, height: 900 },
      hasTouch: mobile, isMobile: mobile, ignoreHTTPSErrors: true });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => {
      window.__audioEvents = [];
      const originalPlay = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function () {
        this.addEventListener("playing", () => window.__audioEvents.push({ kind: "playing", time: performance.now() }), { once: true });
        this.addEventListener("ended", () => window.__audioEvents.push({ kind: "ended", time: performance.now() }), { once: true });
        return originalPlay.call(this);
      };
    });
    await page.route("**/api/v1/speech/settings", route => route.fulfill({ json: {
      provider: "openai-compatible", auto_play: true, voice: "", language: "zh-CN", volume: 1, rate: 1, pitch: 1,
    } }));
    await page.route("**/api/v1/conversations", route => route.fulfill({ json: [conversation] }));
    await page.route(`**/api/v1/conversations/${conversation.id}`, route => route.fulfill({ json: { ...conversation, messages: [] } }));
    await page.route("**/api/v1/conversations/*/messages/stream", route => route.fulfill({ contentType: "text/event-stream", body: [
      `event: user_message\ndata: ${JSON.stringify({ message: message("user", "user", "测试", 1) })}\n\n`,
      `event: delta\ndata: ${JSON.stringify({ content: answer })}\n\n`,
      `event: done\ndata: ${JSON.stringify({ message: message("assistant", "assistant", answer, 2) })}\n\n`,
    ].join("") }));
    const requests = [];
    await page.route("**/api/v1/speech/syntheses", async route => {
      requests.push(route.request().postDataJSON().text);
      await new Promise(resolve => setTimeout(resolve, requests.length === 1 ? 180 : 50));
      await route.fulfill({ contentType: "audio/wav", body: audio });
    });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.locator("textarea").fill("测试连续播报");
    const send = page.getByRole("button", { name: "发送问题", exact: true });
    if (mobile) await send.tap(); else await send.click();
    await page.waitForFunction(() => window.__audioEvents.some(e => e.kind === "playing"));
    expect(requests.length).toBeGreaterThanOrEqual(2);
    expect(await page.evaluate(() => window.__audioEvents.filter(e => e.kind === "ended").length)).toBe(0);
    await page.waitForFunction(() => window.__audioEvents.filter(e => e.kind === "playing").length >= 2);
    const events = await page.evaluate(() => window.__audioEvents);
    const firstEnd = events.find(e => e.kind === "ended");
    const secondStart = events.filter(e => e.kind === "playing")[1];
    expect(firstEnd).toBeTruthy();
    expect(secondStart.time - firstEnd.time).toBeLessThan(500);
    const mute = page.getByRole("button", { name: "关闭自动播报", exact: true });
    if (mobile) {
      await mute.tap();
      const styles = await page.getByRole("button", { name: "开启自动播报", exact: true }).evaluate(element => ({ highlight: getComputedStyle(element).webkitTapHighlightColor,
        outline: getComputedStyle(element).outlineStyle }));
      expect(styles.highlight).toBe("rgba(0, 0, 0, 0)");
      expect(styles.outline).toBe("none");
    } else {
      await mute.click();
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle)).toBe("solid");
    }
    await expect(page.getByRole("button", { name: "开启自动播报", exact: true })).toBeVisible();
    expect(requests.every(text => text.length <= 320)).toBe(true);
    expect(errors).toEqual([]);
    await page.screenshot({ path: `.artifacts/visual-check/${mobile ? "mobile" : "desktop"}-continuous-speech.png` });
    console.log(`${mobile ? "mobile" : "desktop"}: prefetched ${requests.length} segments; native audio gap ${Math.round(secondStart.time - firstEnd.time)} ms; focus and touch feedback passed.`);
    await context.close();
  }
} finally { await browser.close(); }
