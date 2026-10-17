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
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await page.goto(baseUrl, { waitUntil: "networkidle" });

  await page.evaluate(() => {
    const welcome = document.querySelector(".welcome-block");
    const messages = document.createElement("div");
    messages.className = "messages";
    messages.innerHTML = `
      <article class="message-row assistant">
        <div class="message-avatar" aria-hidden="true"></div>
        <div class="message-content">
          <span class="message-author">科普助手</span>
          <div class="message-body" data-markdown-check>
            <p>这是第一段内容，用来检查普通段落之间的距离。</p>
            <p>这是第二段内容，段间距应当清晰但不过分松散。</p>
            <h2>注意事项</h2>
            <ul>
              <li><p>保持局部清洁，并遵循医生建议。</p></li>
              <li><p>如有明显不适，请及时就医。</p></li>
            </ul>
            <hr />
            <blockquote><p>以上内容仅供健康科普。</p></blockquote>
            <p>这是最后一段。</p>
          </div>
        </div>
      </article>`;
    welcome?.replaceWith(messages);
  });

  const result = await page.locator("[data-markdown-check]").evaluate((element) => {
    const paragraphs = [...element.querySelectorAll(":scope > p")];
    return {
      whiteSpace: getComputedStyle(element).whiteSpace,
      paragraphMargins: paragraphs.map(
        (paragraph) => Number.parseFloat(getComputedStyle(paragraph).marginBottom),
      ),
      viewportWidth: window.innerWidth,
      pageWidth: document.documentElement.scrollWidth,
    };
  });

  if (result.whiteSpace !== "normal") {
    throw new Error(`Markdown preserves source whitespace: ${result.whiteSpace}`);
  }
  if (result.paragraphMargins.some((margin) => margin > 8)) {
    throw new Error(`Markdown paragraph spacing is too large: ${result.paragraphMargins}`);
  }
  if (result.pageWidth > result.viewportWidth) {
    throw new Error(`Markdown causes horizontal overflow: ${JSON.stringify(result)}`);
  }

  await page.screenshot({
    path: path.join(outputDir, "mobile-markdown-spacing.png"),
  });
  console.log("Markdown spacing smoke check passed.", result);
} finally {
  await browser.close();
}
