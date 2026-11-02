import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CloudPlayback } from "./cloudPlayback";

class FakeAudio {
  src = "";
  currentTime = 0;
  preload = "";
  volume = 1;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onplaying: (() => void) | null = null;
  play = vi.fn().mockResolvedValue(undefined);
  pause = vi.fn();
  load = vi.fn();
  removeAttribute = vi.fn();
  setAttribute = vi.fn();
  constructor(src = "") { this.src = src; audios.push(this); }
}
let audios: FakeAudio[];
beforeEach(() => {
  audios = [];
  vi.useFakeTimers();
  vi.stubGlobal("Audio", FakeAudio);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function setup() {
  const requests: { text: string; signal: AbortSignal; resolve: (blob: Blob) => void; reject: (error: Error) => void }[] = [];
  const onError = vi.fn();
  const onStart = vi.fn();
  const queue = new CloudPlayback((text, signal) => new Promise<Blob>((resolve, reject) => {
    requests.push({ text, signal, resolve, reject });
  }), () => 0.8, onError, onStart);
  return { queue, requests, onError, onStart };
}
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };
const audioBlob = () => new Blob(["ID3test"], { type: "audio/mpeg" });

describe("cloud speech look-ahead", () => {
  it("starts the first completed sentence immediately without waiting for the answer", async () => {
    const { queue, requests } = setup();
    queue.enqueue("这是第一句完整的科普说明。");
    expect(requests.map(r => r.text)).toEqual(["这是第一句完整的科普说明。"]);
    queue.enqueue("第二句。");
    expect(requests.map(r => r.text)).toEqual(["这是第一句完整的科普说明。", "第二句。"]);
    queue.cancel();
  });

  it("prefetches with bounded concurrency and plays in order even if requests finish out of order", async () => {
    const { queue, requests } = setup();
    for (let n = 0; n < 10; n++) queue.enqueue(String(n).repeat(160));
    queue.finish();
    expect(requests).toHaveLength(2);
    requests[1].resolve(audioBlob()); await tick();
    expect(audios[0].play).not.toHaveBeenCalled();
    expect(requests).toHaveLength(3);
    requests[0].resolve(audioBlob()); await tick();
    expect(audios[0].play).toHaveBeenCalledOnce();
    expect(audios[0].load).toHaveBeenCalledOnce();
    expect(audios[0].volume).toBe(0.8);
    // Next clip is already downloaded: ending playback immediately starts it, with no await.
    audios[0].onended?.();
    expect(audios[0].play).toHaveBeenCalledTimes(2);
    expect(requests.length).toBeLessThanOrEqual(5);
    queue.cancel();
    expect(requests.every(r => r.signal.aborted)).toBe(true);
  });

  it("cancels playing and prefetched clips and discards late responses on session change", async () => {
    const { queue, requests } = setup();
    queue.enqueue("一".repeat(160)); queue.enqueue("二".repeat(160));
    queue.enqueue("三".repeat(160)); queue.finish();
    requests[0].resolve(audioBlob()); await tick();
    const staleEnd = audios[0].onended;
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    queue.cancel();
    expect(audios[0].pause).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledOnce();
    requests[1].resolve(audioBlob()); await tick();
    staleEnd?.();
    expect(audios).toHaveLength(1);
    queue.enqueue("新会话。"); queue.finish();
    expect(requests.at(-1)?.text).toBe("新会话。");
    queue.cancel();
  });

  it("unlocks one reusable player from a user gesture before async synthesis finishes", async () => {
    const { queue, requests } = setup();
    queue.unlock();
    await tick();
    expect(audios).toHaveLength(1);
    expect(audios[0].play).toHaveBeenCalledOnce();
    queue.enqueue("移动端第一句。");
    await vi.advanceTimersByTimeAsync(90);
    requests[0].resolve(audioBlob()); await tick();
    expect(audios[0].play).toHaveBeenCalledTimes(2);
    queue.cancel();
  });

  it("allows a later user gesture to retry an unlock rejected by a mobile WebView", async () => {
    const { queue, requests, onStart } = setup();
    audios[0].play.mockRejectedValueOnce(new Error("autoplay blocked"));
    queue.unlock();
    await tick(); await tick();
    expect(audios[0].play).toHaveBeenCalledOnce();
    queue.unlock();
    await tick(); await tick();
    expect(audios[0].play).toHaveBeenCalledTimes(2);
    queue.enqueue("移动端重新解锁后开始播放。");
    requests[0].resolve(audioBlob());
    await tick();
    expect(audios[0].play).toHaveBeenCalledTimes(3);
    audios[0].onplaying?.();
    expect(onStart).toHaveBeenCalledOnce();
    queue.cancel();
  });

  it("retries a failed segment once before falling back without skipping ahead", async () => {
    const { queue, requests, onError } = setup();
    queue.enqueue("一".repeat(160)); queue.enqueue("二".repeat(160));
    queue.enqueue("三".repeat(160)); queue.finish();
    requests[0].reject(new Error("upstream unavailable")); await tick();
    expect(onError).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(280);
    expect(requests).toHaveLength(3);
    requests[2].reject(new Error("upstream unavailable")); await tick();
    expect(onError).toHaveBeenCalledOnce();
    queue.enqueue("不应继续。"); queue.finish();
    expect(requests).toHaveLength(3);
    queue.cancel();
  });

  it("refills short pending clauses as soon as a saturated playback slot is released", async () => {
    const { queue, requests } = setup();
    for (let n = 0; n < 8; n += 1) queue.enqueue(String(n).repeat(24));
    expect(requests).toHaveLength(2);
    for (let n = 0; n < 3; n += 1) {
      requests[n].resolve(audioBlob());
      await tick();
    }
    expect(requests).toHaveLength(5);
    audios[0].onended?.();
    requests[3].resolve(audioBlob());
    await tick();
    expect(requests).toHaveLength(6);
    expect(requests.every(request => request.text.length <= 64)).toBe(true);
    queue.cancel();
  });
});
