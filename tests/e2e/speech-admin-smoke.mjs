// Uses a running admin server and optional real cloud preview; never saves settings.
// Supply ADMIN_E2E_TOKEN via the environment, not a command-line argument.
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";

const baseUrl = process.env.ADMIN_E2E_BASE_URL ?? "http://127.0.0.1:5174";
const token = process.env.ADMIN_E2E_TOKEN;
if (!token) throw new Error("ADMIN_E2E_TOKEN is required");
const executablePath = [process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].filter(Boolean).find(existsSync);
const browser = await chromium.launch(executablePath ? { executablePath } : {});
await mkdir(".artifacts/visual-check", { recursive: true });
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 } });
    await context.addInitScript(token => sessionStorage.setItem("medical-agent-admin-token", token), token);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(baseUrl);
    for (const label of ["云端语音合成", "设备语音"]) {
      await expect(page.getByRole("row").filter({ hasText: label }).getByRole("button", { name: "探活", exact: true })).toBeEnabled();
    }
    await page.screenshot({ path: `.artifacts/visual-check/${width}-tts-inventory.png`, fullPage: true });
    await page.getByRole("button", { name: "声音设置", exact: true }).click();
    await page.locator('.ant-select[aria-label="播报方式"]').click();
    await page.locator(".ant-select-dropdown").getByText("云端语音 · 自定义音色", { exact: true }).click();
    await expect(page.getByRole("button", { name: "探活当前音色" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "云端预设音色" })).toBeVisible();
    if (width === 1440 && process.env.E2E_REAL_TTS === "1") {
      await page.evaluate(() => {
        window.__audioStarted = false;
        const play = HTMLMediaElement.prototype.play;
        HTMLMediaElement.prototype.play = function () {
          this.addEventListener("playing", () => { window.__audioStarted = true; }, { once: true });
          return play.call(this);
        };
      });
      await page.getByLabel("云端音色 ID").fill("anna");
      const response = page.waitForResponse(r => r.url().endsWith("/speech/preview"));
      await page.getByRole("button", { name: "试听", exact: true }).click();
      expect((await response).status()).toBe(200);
      await page.waitForFunction(() => window.__audioStarted, null, { timeout: 45000 });
      await page.getByRole("button", { name: "停止", exact: true }).click();
    }
    await page.route("**/admin/system/speech/preview", route => route.fulfill({ status: 502, json: { detail: {
      summary: "供应商拒绝请求参数", suggestion: "检查完整音色 ID", http_status: 400,
      upstream_code: "20047", upstream_message: "Invalid voice.", voice: "invalid", model: "CosyVoice-test",
    } } }));
    await page.getByRole("button", { name: "试听", exact: true }).click();
    await expect(page.getByText("供应商说明：Invalid voice.", { exact: true })).toBeVisible();
    await expect(page.getByText("供应商 HTTP：400", { exact: true })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `.artifacts/visual-check/${width}-tts-diagnostics.png`, fullPage: true });

    // Simulate an enumerated but broken online voice and verify the user sees the fallback.
    await page.locator('.ant-select[aria-label="播报方式"]').click();
    await page.locator(".ant-select-dropdown").getByText("设备语音 · 浏览器音色", { exact: true }).click();
    await page.evaluate(() => {
      const synth = window.speechSynthesis;
      Object.defineProperty(synth, "getVoices", { configurable: true, value: () => [
        { name: "不可用在线音色", voiceURI: "broken-online", lang: "zh-CN", localService: false },
        { name: "本机测试音色", voiceURI: "local-test", lang: "zh-CN", localService: true },
      ] });
      Object.defineProperty(window, "SpeechSynthesisUtterance", { configurable: true, value: class { constructor(text) { this.text = text; } } });
      Object.defineProperty(synth, "speak", { configurable: true, value: u => {
        if (u.voice?.voiceURI === "broken-online") setTimeout(() => u.onerror?.({ error: "network" }), 20);
        else setTimeout(() => { u.onstart?.(); setTimeout(() => u.onend?.(), 100); }, 20);
      } });
      synth.dispatchEvent(new Event("voiceschanged"));
    });
    await page.getByLabel("音色名称", { exact: true }).fill("broken-online");
    await page.getByRole("button", { name: "试听", exact: true }).click();
    await expect(page.getByText(/本次改用 本机测试音色/)).toBeVisible();
    await expect(page.getByText("试听完成", { exact: true })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `.artifacts/visual-check/${width}-device-voice-fallback.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    await context.close();
  }
  console.log("Speech admin passed: independent model rows, cloud preview, inline errors, explicit device fallback, desktop/mobile layout.");
} finally { await browser.close(); }
