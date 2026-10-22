import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CloudPlayback } from "./cloudPlayback";

class FakeAudio {
  preload = "";
  volume = 1;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  play = vi.fn().mockResolvedValue(undefined);
  pause = vi.fn();
  load = vi.fn();
  removeAttribute = vi.fn();
  constructor(public src: string) { audios.push(this); }
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
  const queue = new CloudPlayback((text, signal) => new Promise<Blob>((resolve, reject) => {
    requests.push({ text, signal, resolve, reject });
  }), () => 0.8, onError);
  return { queue, requests, onError };
}
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };
const audioBlob = () => new Blob(["ID3test"], { type: "audio/mpeg" });

describe("cloud speech look-ahead", () => {
  it("starts the first completed sentence immediately without waiting for the answer", async () => {
    const { queue, requests } = setup();
    queue.enqueue("第一句。");
    expect(requests.map(r => r.text)).toEqual(["第一句。"]);
    queue.enqueue("第二句。");
    await vi.advanceTimersByTimeAsync(120);
    expect(requests.map(r => r.text)).toEqual(["第一句。", "第二句。"]);
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
    expect(audios[1].play).toHaveBeenCalledOnce();
    expect(audios[0].load).toHaveBeenCalledOnce();
    expect(audios[1].volume).toBe(0.8);
    // Next clip is already downloaded: ending playback immediately starts it, with no await.
    audios[1].onended?.();
    expect(audios[0].play).toHaveBeenCalledOnce();
    expect(requests.length).toBeLessThanOrEqual(4);
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

  it("reports a prefetch failure once and never skips ahead or creates unhandled rejections", async () => {
    const { queue, requests, onError } = setup();
    queue.enqueue("一".repeat(160)); queue.enqueue("二".repeat(160));
    queue.enqueue("三".repeat(160)); queue.finish();
    requests[1].reject(new Error("upstream unavailable")); await tick();
    expect(onError).not.toHaveBeenCalled();
    requests[0].resolve(audioBlob()); await tick();
    audios[0].onended?.();
    expect(onError).toHaveBeenCalledOnce();
    queue.enqueue("不应继续。"); queue.finish();
    expect(requests).toHaveLength(2);
    queue.cancel();
  });
});
