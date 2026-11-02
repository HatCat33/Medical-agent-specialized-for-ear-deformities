import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

import { chromium } from "@playwright/test";

const baseUrl = process.env.E2E_BASE_URL ?? "https://127.0.0.1:5174";
const outputDir = path.resolve(".artifacts", "visual-check");
const browserCandidates = [
  process.env.PLAYWRIGHT_EXECUTABLE_PATH,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean);
const executablePath = browserCandidates.find(candidate => existsSync(candidate));
const now = "2026-10-29T10:00:00+08:00";
const conversations = [
  { id: "older", title: "较早的术后护理咨询", status: "active", created_at: now, updated_at: now, last_message_at: "2026-10-27T10:00:00+08:00" },
  { id: "newest", title: "最新的耳部护理咨询", status: "active", created_at: now, updated_at: now, last_message_at: "2026-10-29T14:20:00+08:00" },
  { id: "middle", title: "听力检查相关咨询", status: "active", created_at: now, updated_at: now, last_message_at: "2026-10-28T09:00:00+08:00" },
];
const messages = [
  { id: "question", conversation_id: "newest", sequence_no: 1, role: "user", content: "孩子术后耳朵周围有一点红，日常护理需要注意什么？", status: "completed", safety_code: null, created_at: now, attachments: [] },
  { id: "answer", conversation_id: "newest", sequence_no: 2, role: "assistant", content: "先保持局部清洁、干燥，避免反复触碰或压到耳部。轻微发红需要结合出现时间和变化情况观察。\n\n可以重点留意：\n\n- 红肿范围是否继续扩大；\n- 有没有渗液、明显疼痛或发热；\n- 耳部颜色是否出现明显变化。\n\n如果红肿持续加重或伴有渗液，建议及时联系手术医院确认。", status: "completed", safety_code: null, created_at: now, attachments: [] },
];

await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}) });

try {
  for (const target of [
    { name: "desktop", width: 1440, height: 900, mobile: false },
    { name: "mobile", width: 390, height: 844, mobile: true },
  ]) {
    const context = await browser.newContext({
      viewport: { width: target.width, height: target.height },
      isMobile: target.mobile,
      hasTouch: target.mobile,
      ignoreHTTPSErrors: true,
    });
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.__copiedMessageText = "";
      window.__spokenMessageText = "";
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: { writeText: async value => { window.__copiedMessageText = value; } },
      });
      class TestUtterance {
        constructor(text) { this.text = text; }
      }
      Object.defineProperty(window, "SpeechSynthesisUtterance", {
        configurable: true,
        value: TestUtterance,
      });
      Object.defineProperty(window, "speechSynthesis", {
        configurable: true,
        value: {
          cancel: () => undefined,
          getVoices: () => [],
          resume: () => undefined,
          speak: utterance => {
            window.__spokenMessageText += utterance.text;
            utterance.onstart?.();
            setTimeout(() => utterance.onend?.(new Event("end")), 0);
          },
        },
      });
    });
    const nativeDialogs = [];
    page.on("dialog", dialog => {
      nativeDialogs.push(dialog.type());
      void dialog.dismiss();
    });
    await page.route("**/api/v1/**", async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (pathname === "/api/v1/speech/settings") {
        return route.fulfill({ json: { provider: "browser", auto_play: false, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1 } });
      }
      if (pathname === "/api/v1/conversations" && request.method() === "GET") {
        return route.fulfill({ json: conversations });
      }
      if (pathname.startsWith("/api/v1/conversations/") && request.method() === "GET") {
        const id = pathname.split("/").at(-1);
        if (id === "middle") await new Promise(resolve => setTimeout(resolve, 350));
        return route.fulfill({
          json: {
            ...conversations.find(item => item.id === id),
            messages: messages.map(message => ({ ...message, conversation_id: id })),
            generation_pending: false,
          },
        });
      }
      if (pathname.startsWith("/api/v1/conversations/") && request.method() === "DELETE") {
        return route.fulfill({ status: 204 });
      }
      return route.fulfill({ status: 404, json: { detail: "not_found" } });
    });

    await page.goto(baseUrl, { waitUntil: "networkidle" });
    const assistantMessage = page.locator(".message-row.assistant .message-body");
    await assistantMessage.waitFor();
    await page.screenshot({ path: path.join(outputDir, `${target.name}-conversation-ui.png`) });
    if (target.mobile) {
      await assistantMessage.dispatchEvent("pointerdown", {
        pointerType: "touch", button: 0, clientX: 100, clientY: 280,
      });
      await page.waitForTimeout(520);
      const messageMenu = page.getByRole("menu", { name: "消息操作" });
      await messageMenu.waitFor();
      const menuBounds = await messageMenu.boundingBox();
      if (!menuBounds || Math.abs(menuBounds.x + menuBounds.width / 2 - 100) > 3 || menuBounds.y > 280) {
        throw new Error(`Message menu was not positioned beside the long press: ${JSON.stringify(menuBounds)}`);
      }
      await assistantMessage.dispatchEvent("pointerup", {
        pointerType: "touch", button: 0, clientX: 100, clientY: 280,
      });
      await page.getByRole("menuitem", { name: "复制消息" }).click();
      const copied = await page.evaluate(() => window.__copiedMessageText);
      if (copied !== messages[1].content) throw new Error("Custom copy action did not copy the full answer.");
      await messageMenu.waitFor({ state: "detached" });

      await assistantMessage.dispatchEvent("pointerdown", {
        pointerType: "touch", button: 0, clientX: 230, clientY: 410,
      });
      await page.waitForTimeout(520);
      await assistantMessage.dispatchEvent("pointerup", {
        pointerType: "touch", button: 0, clientX: 230, clientY: 410,
      });
      await page.screenshot({ path: path.join(outputDir, "mobile-message-action-menu.png") });
      await page.getByRole("menuitem", { name: "朗读消息" }).click();
      await messageMenu.waitFor({ state: "detached" });
      await page.waitForFunction(() => window.__spokenMessageText.includes("及时联系手术医院确认"));
      const spoken = await page.evaluate(() => window.__spokenMessageText);
      if (!spoken.includes("先保持局部清洁") || !spoken.includes("及时联系手术医院确认")) {
        throw new Error("Custom read-aloud action did not receive the complete visible answer.");
      }

      await page.evaluate(() => {
        const makeTouch = (x, y) => new Touch({
          identifier: 1, target: document.body, clientX: x, clientY: y,
          pageX: x, pageY: y, screenX: x, screenY: y,
        });
        const start = makeTouch(220, 420);
        document.dispatchEvent(new TouchEvent("touchstart", {
          bubbles: true, cancelable: true, touches: [start], targetTouches: [start], changedTouches: [start],
        }));
        const move = makeTouch(389, 424);
        document.dispatchEvent(new TouchEvent("touchmove", {
          bubbles: true, cancelable: true, touches: [move], targetTouches: [move], changedTouches: [move],
        }));
        document.dispatchEvent(new TouchEvent("touchend", {
          bubbles: true, cancelable: true, touches: [], targetTouches: [], changedTouches: [move],
        }));
      });
      await page.locator(".app-shell.is-history-open").waitFor();
    }
    const titles = await page.locator(".history-select strong").allTextContents();
    if (titles.join("|") !== "最新的耳部护理咨询|听力检查相关咨询|较早的术后护理咨询") {
      throw new Error(`History order is incorrect: ${titles.join("|")}`);
    }
    const layout = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
    if (layout.scrollWidth > layout.width) throw new Error(`Horizontal overflow: ${JSON.stringify(layout)}`);
    await page.screenshot({ path: path.join(outputDir, `${target.name}-patient-ui.png`) });

    await page.getByRole("button", { name: "删除会话：最新的耳部护理咨询" }).click();
    const dialog = page.getByRole("dialog", { name: "删除这条会话？" });
    await dialog.waitFor();
    await page.waitForTimeout(220);
    const dialogIsTopLayer = await dialog.evaluate(element => {
      const bounds = element.getBoundingClientRect();
      const points = [
        [bounds.left + 8, bounds.top + 8],
        [bounds.right - 8, bounds.top + 8],
        [bounds.left + 8, bounds.bottom - 8],
        [bounds.right - 8, bounds.bottom - 8],
      ];
      return points.every(([x, y]) => document.elementFromPoint(x, y)?.closest(".confirm-dialog") === element);
    });
    if (!dialogIsTopLayer) throw new Error("Delete dialog is covered by another interface layer.");
    await page.screenshot({ path: path.join(outputDir, `${target.name}-delete-dialog.png`) });
    if (nativeDialogs.length) throw new Error(`Native dialog was opened: ${nativeDialogs.join(",")}`);
    await page.getByRole("button", { name: "取消", exact: true }).click();
    await page.getByRole("dialog", { name: "删除这条会话？" }).waitFor({ state: "detached" });
    await page.locator(".history-select", { hasText: "听力检查相关咨询" }).click();
    await page.waitForTimeout(80);
    if (await page.getByText("正在同步上一条回答", { exact: true }).isVisible()) {
      throw new Error("A completed history conversation displayed the answer-syncing state while loading.");
    }
    await page.locator(".history-item.is-active .history-select", { hasText: "听力检查相关咨询" }).waitFor();
    await context.close();
  }
  const emptyContext = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true });
  const emptyPage = await emptyContext.newPage();
  await emptyPage.addInitScript(() => {
    window.__testPickerCalls = 0;
    window.showOpenFilePicker = async () => {
      window.__testPickerCalls += 1;
      return [];
    };
  });
  emptyPage.on("filechooser", () => { throw new Error("Native file chooser opened before an in-app upload choice was selected."); });
  let emptyCreateRequests = 0;
  let emptyFaqRequests = 0;
  await emptyPage.route("**/api/v1/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/speech/settings") return route.fulfill({ json: { provider: "browser", auto_play: false, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1 } });
    if (pathname === "/api/v1/conversations" && request.method() === "GET") return route.fulfill({ json: [] });
    if (pathname === "/api/v1/conversations" && request.method() === "POST") {
      emptyCreateRequests += 1;
      return route.fulfill({ status: 201, json: { id: "empty", title: "新会话", status: "active", created_at: now, updated_at: now, last_message_at: null } });
    }
    if (pathname === "/api/v1/conversations/empty" && request.method() === "GET") return route.fulfill({ json: { id: "empty", title: "新会话", status: "active", created_at: now, updated_at: now, last_message_at: null, messages: [] } });
    if (pathname === "/api/v1/conversations/empty/messages/stream" && request.method() === "POST") {
      emptyFaqRequests += 1;
      const body = await request.postDataJSON();
      const userMessage = { id: "faq-user", conversation_id: "empty", sequence_no: 1, role: "user", content: body.content, status: "completed", safety_code: null, created_at: now, attachments: [] };
      const assistantMessage = { id: "faq-answer", conversation_id: "empty", sequence_no: 2, role: "assistant", content: "功能锻炼时应按接诊医生给出的时间和方式进行。", status: "completed", safety_code: null, created_at: now, attachments: [] };
      const streamBody = [
        `event: user_message\ndata: ${JSON.stringify({ message: userMessage })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ content: assistantMessage.content })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ message: assistantMessage })}\n\n`,
      ].join("");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: streamBody });
    }
    return route.fulfill({ status: 404, json: { detail: "not_found" } });
  });
  await emptyPage.goto(baseUrl, { waitUntil: "networkidle" });
  await emptyPage.getByRole("heading", { name: /耳畸形科普助手/ }).waitFor();
  await emptyPage.screenshot({ path: path.join(outputDir, "mobile-new-conversation.png") });
  await emptyPage.locator(".new-conversation-trigger").click();
  await emptyPage.locator(".new-conversation-trigger").click();
  if (emptyCreateRequests !== 1) throw new Error(`Repeated empty conversations were created: ${emptyCreateRequests}`);
  await emptyPage.getByRole("button", { name: "添加图片" }).click();
  await emptyPage.getByRole("dialog", { name: "添加图片" }).waitFor();
  await emptyPage.locator(".media-sheet-backdrop").click({ position: { x: 5, y: 5 } });
  if (!await emptyPage.getByRole("dialog", { name: "添加图片" }).isVisible()) throw new Error("Media picker closed on a backdrop touch.");
  const emptyLayout = await emptyPage.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }));
  if (emptyLayout.scrollWidth > emptyLayout.width) throw new Error(`Empty page horizontal overflow: ${JSON.stringify(emptyLayout)}`);
  const sheetIsTopLayer = await emptyPage.evaluate(() => {
    const sheet = document.querySelector(".media-sheet");
    if (!sheet || sheet.parentElement?.parentElement !== document.body) return false;
    const bounds = sheet.getBoundingClientRect();
    return document.elementFromPoint(bounds.left + 8, bounds.bottom - 8)?.closest(".media-sheet") === sheet;
  });
  if (!sheetIsTopLayer) throw new Error("Mobile media picker is covered by the composer.");
  await emptyPage.waitForTimeout(220);
  await emptyPage.screenshot({ path: path.join(outputDir, "mobile-media-picker.png") });
  await emptyPage.getByRole("button", { name: "从相册选择" }).click();
  const pickerCalls = await emptyPage.evaluate(() => window.__testPickerCalls);
  if (pickerCalls !== 1) throw new Error(`Modern gallery picker was not used: ${pickerCalls}`);
  await emptyPage.getByRole("button", { name: /术后活动/ }).click();
  await emptyPage.getByText("功能锻炼时应按接诊医生给出的时间和方式进行。").waitFor();
  if (emptyCreateRequests !== 1 || emptyFaqRequests !== 1) {
    throw new Error(`First FAQ did not create and send exactly once: create=${emptyCreateRequests}, send=${emptyFaqRequests}`);
  }
  await emptyContext.close();

  const isolationContext = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true,
  });
  const isolationPage = await isolationContext.newPage();
  await isolationPage.addInitScript(() => {
    window.__isolatedSpeech = "";
    class TestUtterance {
      constructor(text) { this.text = text; }
    }
    Object.defineProperty(window, "SpeechSynthesisUtterance", {
      configurable: true,
      value: TestUtterance,
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: {
        cancel: () => undefined,
        getVoices: () => [],
        resume: () => undefined,
        speak: utterance => {
          window.__isolatedSpeech += utterance.text;
          utterance.onstart?.();
          setTimeout(() => utterance.onend?.(new Event("end")), 0);
        },
      },
    });
  });
  const oldConversation = {
    id: "old-pending", title: "上一次咨询", status: "active", created_at: now,
    updated_at: now, last_message_at: now,
  };
  const newConversation = {
    id: "new-isolated", title: "新会话", status: "active", created_at: now,
    updated_at: now, last_message_at: null,
  };
  const oldUserMessage = {
    id: "old-user", conversation_id: oldConversation.id, sequence_no: 1, role: "user",
    content: "第一个会话的问题", status: "completed", safety_code: null, created_at: now, attachments: [],
  };
  let newConversationCreated = false;
  await isolationPage.route("**/api/v1/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/speech/settings") {
      return route.fulfill({ json: { provider: "browser", auto_play: true, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1 } });
    }
    if (pathname === "/api/v1/conversations" && request.method() === "GET") {
      return route.fulfill({ json: newConversationCreated ? [newConversation, oldConversation] : [oldConversation] });
    }
    if (pathname === "/api/v1/conversations" && request.method() === "POST") {
      newConversationCreated = true;
      return route.fulfill({ status: 201, json: newConversation });
    }
    if (pathname === "/api/v1/conversations/old-pending" && request.method() === "GET") {
      return route.fulfill({ json: { ...oldConversation, messages: [oldUserMessage], generation_pending: true } });
    }
    if (pathname === "/api/v1/conversations/new-isolated" && request.method() === "GET") {
      return route.fulfill({ json: { ...newConversation, messages: [], generation_pending: false } });
    }
    if (pathname === "/api/v1/conversations/new-isolated/messages/stream" && request.method() === "POST") {
      const payload = await request.postDataJSON();
      const userMessage = { ...oldUserMessage, id: "new-user", conversation_id: newConversation.id, content: payload.content };
      const assistantMessage = { ...userMessage, id: "new-answer", sequence_no: 2, role: "assistant", content: "这是第二个会话的回答。" };
      const streamBody = [
        `event: user_message\ndata: ${JSON.stringify({ message: userMessage })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ content: assistantMessage.content })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ message: assistantMessage })}\n\n`,
      ].join("");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: streamBody });
    }
    return route.fulfill({ status: 404, json: { detail: "not_found" } });
  });
  await isolationPage.goto(baseUrl, { waitUntil: "networkidle" });
  await isolationPage.getByText("正在同步上一条回答", { exact: true }).waitFor();
  await isolationPage.locator(".new-conversation-trigger").click();
  await isolationPage.getByRole("heading", { name: /耳畸形科普助手/ }).waitFor();
  await isolationPage.getByLabel("咨询问题").fill("第二个会话的问题");
  await isolationPage.getByRole("button", { name: "发送问题" }).click();
  await isolationPage.getByText("这是第二个会话的回答。").waitFor();
  await isolationPage.waitForFunction(() => window.__isolatedSpeech.includes("这是第二个会话的回答"));
  const isolatedSpeech = await isolationPage.evaluate(() => window.__isolatedSpeech);
  if (isolatedSpeech.includes("第一个会话") || await isolationPage.getByText("正在同步上一条回答", { exact: true }).isVisible()) {
    throw new Error(`Conversation speech was not isolated from background synchronization: ${isolatedSpeech}`);
  }
  await isolationContext.close();

  const scrollContext = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true,
  });
  const scrollPage = await scrollContext.newPage();
  const scrollConversation = {
    id: "scroll-test", title: "流式阅读测试", status: "active", created_at: now, updated_at: now,
    last_message_at: now,
  };
  const scrollHistory = Array.from({ length: 18 }, (_, index) => ({
    id: `scroll-history-${index + 1}`,
    conversation_id: scrollConversation.id,
    sequence_no: index + 1,
    role: index % 2 === 0 ? "user" : "assistant",
    content: index % 2 === 0
      ? `这是第 ${Math.floor(index / 2) + 1} 个历史问题，想了解孩子日常护理时需要留意哪些变化。`
      : "日常可以留意局部颜色、肿胀范围、是否有渗液，以及孩子是否出现明显疼痛。出现持续变化时应联系接诊医院确认。",
    status: "completed", safety_code: null, created_at: now, attachments: [],
  }));
  const streamedAnswer = "流式回答会继续逐字增加。此时用户正在向上查看较早的对话内容，页面不应突然跳回底部，也不应随着新文字反复上下抖动。";
  await scrollPage.route("**/api/v1/**", route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/speech/settings") return route.fulfill({ json: { provider: "browser", auto_play: false, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1 } });
    if (pathname === "/api/v1/conversations" && request.method() === "GET") return route.fulfill({ json: [scrollConversation] });
    if (pathname === "/api/v1/conversations/scroll-test" && request.method() === "GET") {
      return route.fulfill({ json: { ...scrollConversation, messages: scrollHistory } });
    }
    if (pathname === "/api/v1/conversations/scroll-test/messages/stream" && request.method() === "POST") {
      const userMessage = { id: "scroll-user", conversation_id: "scroll-test", sequence_no: 19, role: "user", content: "继续说明护理重点", status: "completed", safety_code: null, created_at: now, attachments: [] };
      const assistantMessage = { id: "scroll-answer", conversation_id: "scroll-test", sequence_no: 20, role: "assistant", content: streamedAnswer, status: "completed", safety_code: null, created_at: now, attachments: [] };
      const body = [
        `event: user_message\ndata: ${JSON.stringify({ message: userMessage })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ content: streamedAnswer })}\n\n`,
        `event: done\ndata: ${JSON.stringify({ message: assistantMessage })}\n\n`,
      ].join("");
      return route.fulfill({ status: 200, contentType: "text/event-stream", body });
    }
    return route.fulfill({ status: 404, json: { detail: "not_found" } });
  });
  await scrollPage.goto(baseUrl, { waitUntil: "networkidle" });
  await scrollPage.getByLabel("咨询问题").fill("继续说明护理重点");
  await scrollPage.getByRole("button", { name: "发送问题" }).click();
  await scrollPage.locator(".message-body.is-streaming").waitFor();
  await scrollPage.waitForTimeout(350);
  const readingPosition = await scrollPage.locator(".message-scroll").evaluate(element => {
    element.scrollTop = Math.max(100, element.scrollHeight - element.clientHeight - 420);
    element.dispatchEvent(new WheelEvent("wheel", { deltaY: -160, bubbles: true }));
    element.dispatchEvent(new Event("scroll", { bubbles: true }));
    return element.scrollTop;
  });
  await scrollPage.waitForTimeout(900);
  const positionAfterMoreText = await scrollPage.locator(".message-scroll").evaluate(element => element.scrollTop);
  if (Math.abs(positionAfterMoreText - readingPosition) > 3) {
    throw new Error(`Streaming answer moved the user's reading position: ${readingPosition} -> ${positionAfterMoreText}`);
  }
  await scrollPage.screenshot({ path: path.join(outputDir, "mobile-streaming-scroll-stable.png") });
  await scrollPage.getByRole("button", { name: "停止生成" }).click();
  await scrollContext.close();

  const wecomContext = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true,
    userAgent: "Mozilla/5.0 MicroMessenger/7.0 wxwork/4.1.0",
  });
  const wecomPage = await wecomContext.newPage();
  await wecomPage.addInitScript(() => {
    const readyCallbacks = [];
    window.wx = {
      ready: callback => readyCallbacks.push(callback),
      error: () => undefined,
      config: () => queueMicrotask(() => readyCallbacks.splice(0).forEach(callback => callback())),
      agentConfig: options => options.success(),
      checkJsApi: options => options.success({ checkResult: { chooseImage: true, getLocalImgData: true } }),
      chooseImage: options => options.success({ localIds: ["local-1", "local-2", "local-3"] }),
      getLocalImgData: options => options.success({ localData: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" }),
    };
  });
  await wecomPage.route("**/api/v1/**", route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/v1/speech/settings") return route.fulfill({ json: { provider: "browser", auto_play: false, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1 } });
    if (pathname === "/api/v1/channels/wecom-h5/js-sdk-config") return route.fulfill({ json: { corp_id: "corp", agent_id: "1000002", timestamp: 1, nonce: "nonce", signature: "signature", agent_signature: "agent-signature", url: "https://example.test/" } });
    if (pathname === "/api/v1/conversations" && request.method() === "GET") return route.fulfill({ json: [] });
    if (pathname === "/api/v1/conversations" && request.method() === "POST") return route.fulfill({ status: 201, json: { id: "wecom-empty", title: "新会话", status: "active", created_at: now, updated_at: now, last_message_at: null } });
    if (pathname === "/api/v1/conversations/wecom-empty" && request.method() === "GET") return route.fulfill({ json: { id: "wecom-empty", title: "新会话", status: "active", created_at: now, updated_at: now, last_message_at: null, messages: [] } });
    return route.fulfill({ status: 404, json: { detail: "not_found" } });
  });
  await wecomPage.goto(baseUrl, { waitUntil: "networkidle" });
  await wecomPage.getByRole("button", { name: "添加图片" }).click();
  await wecomPage.getByRole("button", { name: "从相册选择" }).click();
  await wecomPage.locator(".selected-image").first().waitFor();
  if (await wecomPage.locator(".selected-image").count() !== 3) throw new Error("Enterprise WeChat multi-image selection did not preserve all images.");
  await wecomPage.screenshot({ path: path.join(outputDir, "mobile-wecom-multi-image.png") });
  await wecomContext.close();
  console.log("Patient UI mock smoke check passed for desktop and mobile.");
} finally {
  await browser.close();
}
