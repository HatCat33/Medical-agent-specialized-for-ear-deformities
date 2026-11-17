// Run backend/scripts/serve_tts_probe.py first. Uses real TTS with synthetic text only.
import { existsSync } from "node:fs";
import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "https://127.0.0.1:5173";
const probeUrl = process.env.TTS_PROBE_URL ?? "http://127.0.0.1:18669";
const mobile = process.env.TTS_PROBE_DEVICE !== "desktop";
const settings = await fetch(`${probeUrl}/settings`).then(response => response.json());
const executablePath = [process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].filter(Boolean).find(existsSync);
const now = new Date().toISOString();
const conversation = { id: "real-tts-probe", title: "测试", created_at: now, updated_at: now };
const answer = "这是一次语音播放测试，请保持自然的语速。我们正在检查文字和声音是否同步呈现，以及不同句子之间有没有明显的等待。测试使用固定的示例文字，不包含患者资料，现在即将完成。";
const message = (role, content) => ({ id: role, role, content, sequence_no: role === "user" ? 1 : 2,
  conversation_id: conversation.id, created_at: now, status: "completed", attachments: [] });
const browser = await chromium.launch(executablePath ? { executablePath } : {});
try {
  const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile,
    hasTouch: true, ignoreHTTPSErrors: true });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(({ probeUrl }) => {
    window.__realTts = { requests: [], sources: [], firstRequest: null };
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : String(input);
      if (!url.includes("/speech/syntheses/stream")) return originalFetch(input, init);
      const record = { text: JSON.parse(init.body).text, started: performance.now() };
      window.__realTts.requests.push(record);
      window.__realTts.firstRequest ??= record.started;
      const response = await originalFetch(`${probeUrl}/stream`, init);
      record.headers = performance.now();
      record.status = response.status;
      const reader = response.body.getReader();
      return new Response(new ReadableStream({
        async pull(controller) {
          try {
            const { value, done } = await reader.read();
            if (done) { record.done = performance.now(); controller.close(); }
            else controller.enqueue(value);
          } catch (error) { record.failed = true; controller.error(error); }
        },
        cancel() { return reader.cancel(); },
      }), { status: response.status, headers: response.headers });
    };
    const originalCreate = AudioContext.prototype.createBufferSource;
    AudioContext.prototype.createBufferSource = function () {
      // eslint-disable-next-line @typescript-eslint/no-this-alias -- Track the owning clock inside source.start().
      const audioContext = this;
      const source = originalCreate.call(this);
      const originalStart = source.start.bind(source);
      source.start = (...args) => {
        const at = args[0] ?? audioContext.currentTime;
        const record = { at, duration: source.buffer.duration,
          wallStart: performance.now() + (at - audioContext.currentTime) * 1000,
          state: audioContext.state,
          textLength: document.querySelector(".message-row.assistant .message-body")?.textContent?.length ?? 0 };
        source.addEventListener("ended", () => { record.ended = performance.now(); });
        window.__realTts.sources.push(record);
        return originalStart(...args);
      };
      return source;
    };
  }, { probeUrl });
  await page.route("**/api/v1/speech/settings", route => route.fulfill({ json: settings }));
  await page.route("**/api/v1/conversations", route => route.fulfill({ json: [conversation] }));
  await page.route(`**/api/v1/conversations/${conversation.id}`, route => route.fulfill({ json: { ...conversation, messages: [] } }));
  await page.route("**/api/v1/conversations/*/messages/stream", route => route.fulfill({ contentType: "text/event-stream", body: [
    `event: user_message\ndata: ${JSON.stringify({ message: message("user", "测试") })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ content: answer })}\n\n`,
    `event: done\ndata: ${JSON.stringify({ message: message("assistant", answer) })}\n\n`,
  ].join("") }));
  let fallbacks = 0;
  await page.route("**/api/v1/speech/syntheses", route => { fallbacks++; return route.fulfill({ status: 503 }); });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator("textarea").fill("测试声音");
  await page.getByRole("button", { name: "发送问题", exact: true }).tap();
  await page.waitForFunction(() => window.__realTts.sources.length > 0, null, { timeout: 40000 });
  await expect(page.locator(".message-row.assistant .message-body")).toHaveText(answer, { timeout: 90000 });
  await page.waitForFunction(() => {
    const sources = window.__realTts.sources;
    const requests = window.__realTts.requests;
    return sources.length > 0 && sources.every(item => item.ended)
      && requests.length > 0 && requests.every(item => item.done);
  }, null, { timeout: 90000 });
  const result = await page.evaluate(() => window.__realTts);
  const sources = result.sources;
  const gaps = sources.slice(1).map((item, index) => Math.max(0, (item.at - sources[index].at - sources[index].duration) * 1000));
  const report = { first_audio_ms: Math.round(sources[0].wallStart - result.firstRequest),
    first_audio_text_characters: sources[0].textLength, total_characters: answer.length,
    max_scheduled_gap_ms: Math.round(Math.max(0, ...gaps)), audio_chunks: sources.length,
    requests: result.requests.map(item => ({ characters: item.text.length, header_ms: Math.round(item.headers - item.started), status: item.status })),
    all_sources_ran: sources.every(item => item.state === "running" && item.ended), fallbacks, errors };
  console.log(JSON.stringify(report, null, 2));
  expect(report.all_sources_ran).toBe(true);
  expect(fallbacks).toBe(0);
  expect(errors).toEqual([]);
  // Text is independent of cloud latency; record its lead without gating it on audio.
  expect(report.max_scheduled_gap_ms).toBeLessThan(500);
  await context.close();
} finally { await browser.close(); }
