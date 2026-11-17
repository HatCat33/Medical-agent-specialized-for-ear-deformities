import { existsSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "https://127.0.0.1:5173";
const executablePath = [process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].filter(Boolean).find(existsSync);
const now = new Date().toISOString();
const conversation = { id: "pcm-stream", title: "PCM 流测试", created_at: now, updated_at: now, last_message_at: now };
const answer = "孩子日常护理需要保持局部清洁干燥，并注意观察有没有红肿或渗液。出现明显异常时，建议及时联系接诊医生。";
const message = (id, role, content, sequence_no) => ({ id, role, content, sequence_no,
  conversation_id: conversation.id, created_at: now, status: "completed", attachments: [] });

const browser = await chromium.launch(executablePath ? { executablePath } : {});
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true,
    hasTouch: true, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const pageErrors = [];
  let mp3Fallbacks = 0;
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.addInitScript(() => {
    window.__pcmEvents = [];
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
      if (!url.includes("/api/v1/speech/syntheses/stream")) return originalFetch(input, init);
      const bytes = new Uint8Array(48000 * 2);
      const view = new DataView(bytes.buffer);
      for (let index = 0; index < 48000; index += 1) view.setInt16(index * 2, 1200, true);
      return new Response(new ReadableStream({
        start(controller) {
          setTimeout(() => { window.__pcmEvents.push({ kind: "chunk", time: performance.now() }); controller.enqueue(bytes.slice(0, 48000)); }, 70);
          setTimeout(() => { window.__pcmEvents.push({ kind: "chunk", time: performance.now() }); controller.enqueue(bytes.slice(48000)); }, 150);
          setTimeout(() => { window.__pcmEvents.push({ kind: "closed", time: performance.now() }); controller.close(); }, 230);
        },
      }), { status: 200, headers: { "Content-Type": "application/octet-stream" } });
    };
    const originalCreate = AudioContext.prototype.createBufferSource;
    AudioContext.prototype.createBufferSource = function () {
      const source = originalCreate.call(this);
      const originalStart = source.start.bind(source);
      source.start = (...args) => {
        window.__pcmEvents.push({ kind: "scheduled", time: performance.now(), at: args[0] });
        return originalStart(...args);
      };
      return source;
    };
  });
  await page.route("**/api/v1/speech/settings", route => route.fulfill({ json: {
    provider: "openai-compatible", auto_play: true, voice: "", cloud_voice: "test", language: "zh-CN",
    volume: 1, rate: 1, pitch: 1, streaming_enabled: true, stream_format: "pcm_s16le",
    stream_sample_rate: 24000, stream_channels: 1,
  } }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: [conversation] }));
  await page.route(`**/api/v1/conversations/${conversation.id}`, route => route.fulfill({ json: { ...conversation, messages: [] } }));
  await page.route("**/api/v1/conversations/*/messages/stream", route => route.fulfill({ contentType: "text/event-stream", body: [
    `event: user_message\ndata: ${JSON.stringify({ message: message("user", "user", "测试", 1) })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ content: answer })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ message: message("assistant", "assistant", answer, 2) })}\n\n`,
  ].join("") }));
  await page.route("**/api/v1/speech/syntheses", route => { mp3Fallbacks += 1; return route.fulfill({ status: 500 }); });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator("textarea").fill("测试流式语音");
  await page.getByRole("button", { name: "发送问题", exact: true }).tap();
  await page.waitForFunction(() => window.__pcmEvents.filter(event => event.kind === "scheduled").length >= 2);
  await page.waitForFunction(() => window.__pcmEvents.some(event => event.kind === "closed"));
  const events = await page.evaluate(() => window.__pcmEvents);
  const firstScheduled = events.find(event => event.kind === "scheduled");
  const firstClosed = events.find(event => event.kind === "closed");
  expect(firstScheduled).toBeTruthy();
  expect(firstClosed).toBeTruthy();
  expect(firstScheduled.time).toBeLessThan(firstClosed.time);
  expect(mp3Fallbacks).toBe(0);
  expect(pageErrors).toEqual([]);
  console.log(`PCM stream started ${Math.round(firstClosed.time - firstScheduled.time)} ms before the first response body completed; no MP3 fallback.`);
  await context.close();
} finally { await browser.close(); }
