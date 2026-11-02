import { afterEach, describe, expect, it, vi } from "vitest";

import { ProgressiveTextStream } from "./progressiveStream";

afterEach(() => vi.useRealTimers());

describe("progressive text stream", () => {
  it("uses a moderate independent default pace", async () => {
    vi.useFakeTimers();
    const output: string[] = [];
    const stream = new ProgressiveTextStream(text => output.push(text), new AbortController().signal);
    stream.push("一二三四五六七八九十");
    const finished = stream.finish();
    await vi.advanceTimersByTimeAsync(290);
    expect(output.join("")).toBe("一二三");
    await vi.runAllTimersAsync();
    await finished;
    expect(output.join("")).toBe("一二三四五六七八九十");
  });
  it("meters a large provider chunk instead of dumping it at once", async () => {
    vi.useFakeTimers();
    const output: string[] = [];
    const stream = new ProgressiveTextStream(text => output.push(text), new AbortController().signal, 16, 16);
    stream.push("模型一次返回了很多内容。");
    const finished = stream.finish();
    expect(output).toEqual([]);
    await vi.advanceTimersByTimeAsync(16);
    expect(output.join("")).toBe("模");
    await vi.runAllTimersAsync();
    await finished;
    expect(output.join("")).toBe("模型一次返回了很多内容。");
  });

  it("preserves unicode characters and settles cleanly when cancelled", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const output: string[] = [];
    const stream = new ProgressiveTextStream(text => output.push(text), controller.signal, 16, 16);
    stream.push("耳朵🙂护理");
    await vi.advanceTimersByTimeAsync(32);
    controller.abort();
    await stream.finish();
    expect(output.join("")).toBe("耳朵");
  });

  it("emits exactly one character per tick even with a large backlog", async () => {
    vi.useFakeTimers();
    const output: string[] = [];
    const stream = new ProgressiveTextStream(text => output.push(text), new AbortController().signal, 28, 180);
    stream.push("一二三四五六七八九十".repeat(80));
    const finished = stream.finish();
    await vi.advanceTimersByTimeAsync(179);
    expect(output).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(output).toEqual(["一"]);
    await vi.advanceTimersByTimeAsync(280);
    expect(output.every(chunk => Array.from(chunk).length === 1)).toBe(true);
    expect(output.length).toBe(11);
    stream.cancel();
    await finished;
  });
});
