import { afterEach, describe, expect, it, vi } from "vitest";

import { Pcm16Decoder, PcmStreamPlayback } from "./pcmStreamPlayback";
import { AnswerPlayback, defaultSpeechSettings } from "./playback";
import { ProgressiveTextStream } from "./progressiveStream";

function pcm(value: number, frames = 1920) {
  const bytes = new Uint8Array(frames * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < frames; index += 1) view.setInt16(index * 2, value, true);
  return bytes;
}

function stream(bytes: Uint8Array) {
  return new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(bytes); controller.close(); },
  });
}

function audioContextMock() {
  const starts: number[] = [];
  const scheduleTimes: number[] = [];
  const stops: ReturnType<typeof vi.fn>[] = [];
  const contexts: Context[] = [];
  class Context {
    constructor() { contexts.push(this); }
    currentTime = 1;
    state: AudioContextState = "running";
    destination = {} as AudioDestinationNode;
    resume = vi.fn().mockResolvedValue(undefined);
    createGain() { return { gain: { value: 1 }, connect: vi.fn() } as unknown as GainNode; }
    createBuffer(channels: number, frames: number, sampleRate: number) {
      const data = Array.from({ length: channels }, () => new Float32Array(frames));
      return {
        duration: frames / sampleRate,
        getChannelData: (channel: number) => data[channel],
      } as AudioBuffer;
    }
    createBufferSource() {
      const stop = vi.fn(); stops.push(stop);
      const source = {
        buffer: null as AudioBuffer | null,
        connect: vi.fn(), stop,
        start: vi.fn(function (this: { buffer: AudioBuffer | null }, at: number) {
          starts.push(this.buffer?.getChannelData(0)[0] ?? at);
          scheduleTimes.push(at);
        }),
        onended: null,
      };
      return source as unknown as AudioBufferSourceNode;
    }
  }
  vi.stubGlobal("window", { AudioContext: Context });
  return { starts, stops, scheduleTimes, clock: () => contexts[0] };
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("PCM stream playback", () => {
  it("waits for a natural phrase instead of splitting a person's name", async () => {
    vi.useFakeTimers();
    audioContextMock();
    const synthesize = vi.fn(async () => stream(pcm(1000)));
    const playback = new AnswerPlayback(vi.fn(), undefined, vi.fn(), synthesize);
    playback.configure({ ...defaultSpeechSettings, provider: "openai-compatible", streaming_enabled: true });
    playback.begin();
    playback.append("建议联系张雨桐");
    await vi.advanceTimersByTimeAsync(500);
    expect(synthesize).not.toHaveBeenCalled();
    playback.append("主任进一步评估孩子目前的耳廓情况，");
    await vi.advanceTimersByTimeAsync(80);
    expect(synthesize).toHaveBeenCalledWith(
      "建议联系张雨桐主任进一步评估孩子目前的耳廓情况，",
      expect.any(AbortSignal),
    );
    playback.cancel();
  });

  it("starts with one short clause and batches the rest without losing text", async () => {
    audioContextMock();
    const synthesize = vi.fn(async () => stream(pcm(1000)));
    const playback = new AnswerPlayback(vi.fn(), undefined, vi.fn(), synthesize);
    playback.configure({ ...defaultSpeechSettings, provider: "openai-compatible", streaming_enabled: true });
    playback.begin();
    const text = "这是一段用于语音测试的文字，请保持自然语速。后续内容应该按完整句子发送，而不是每五个字就发一次请求，这样可以减少等待。";
    playback.append(text); playback.finish();
    await vi.waitFor(() => expect(
      synthesize.mock.calls.map(args => (args as unknown as [string])[0]).join(""),
    ).toBe(text));
    const sent = synthesize.mock.calls.map(args => (args as unknown as [string])[0]);
    expect(sent.join("")).toBe(text);
    expect(sent[0]).toBe("这是一段用于语音测试的文字，");
    expect(sent.every(item => item.length >= 8)).toBe(true);
    playback.cancel();
  });

  it("merges a tiny trailing fragment into the preceding cloud request", async () => {
    vi.useFakeTimers();
    audioContextMock();
    const synthesize = vi.fn(async () => stream(pcm(1000)));
    const playback = new PcmStreamPlayback(synthesize, () => 1, vi.fn(), vi.fn());
    playback.enqueue("孩子目前可以继续观察，");
    playback.enqueue("张医生。 ");
    await vi.advanceTimersByTimeAsync(80);
    expect(synthesize).toHaveBeenCalledWith(
      "孩子目前可以继续观察，张医生。 ",
      expect.any(AbortSignal),
    );
    playback.cancel();
  });

  it("finishes readable text at its own pace while cloud audio is still pending", async () => {
    vi.useFakeTimers();
    audioContextMock();
    const synthesize = () => new Promise<ReadableStream<Uint8Array>>(() => undefined);
    const playback = new AnswerPlayback(vi.fn(), undefined, vi.fn(), synthesize);
    playback.configure({ ...defaultSpeechSettings, provider: "openai-compatible", streaming_enabled: true });
    const text = "这是一段需要等待语音生成的测试内容。";
    let output = "";
    const presenter = new ProgressiveTextStream(chunk => { output += chunk; }, new AbortController().signal);
    playback.begin(); playback.append(text); playback.finish();
    presenter.push(text);
    const finished = presenter.finish();
    await vi.advanceTimersByTimeAsync(290);
    expect(output).toBe(text.slice(0, 3));
    await vi.advanceTimersByTimeAsync(2000);
    await finished;
    expect(output).toBe(text);
    playback.cancel();
  });
  it("preserves odd network bytes and channel frame boundaries", () => {
    const decoder = new Pcm16Decoder();
    expect(decoder.decode(new Uint8Array([0xff]), 1)).toHaveLength(0);
    const values = decoder.decode(new Uint8Array([0x7f, 0x00, 0x80]), 1);
    expect(values).toHaveLength(2);
    expect(values[0]).toBeCloseTo(32767 / 32767);
    expect(values[1]).toBeCloseTo(-1);

    decoder.reset();
    expect(decoder.decode(new Uint8Array([1, 0]), 2)).toHaveLength(0);
    expect(decoder.decode(new Uint8Array([2, 0]), 2)).toHaveLength(2);
  });

  it("plays concurrent requests in text order even when the second response arrives first", async () => {
    const { starts, scheduleTimes, clock } = audioContextMock();
    let resolveFirst: (value: ReadableStream<Uint8Array>) => void = () => undefined;
    const synthesize = vi.fn((text: string) => text.startsWith("第一")
      ? new Promise<ReadableStream<Uint8Array>>(resolve => { resolveFirst = resolve; })
      : Promise.resolve(stream(pcm(2000))));
    const playback = new PcmStreamPlayback(synthesize, () => 1, vi.fn(), vi.fn());
    playback.configure({ sampleRate: 24000, channels: 1 });
    playback.enqueue("第一段需要先进行播放。");
    playback.enqueue("第二段虽然先返回也要等待。");
    playback.finish();
    await Promise.resolve(); await Promise.resolve();
    expect(starts).toEqual([]);
    resolveFirst(stream(pcm(1000)));
    await vi.waitFor(() => expect(starts).toHaveLength(2));
    expect(starts[0]).toBeCloseTo(1000 / 32767);
    expect(starts[1]).toBeCloseTo(2000 / 32767);
    expect(scheduleTimes[1] - scheduleTimes[0]).toBeCloseTo(1920 / 24000);
    clock().currentTime = scheduleTimes[0] + 0.04;
    expect(playback.progress()).toBeCloseTo(Array.from("第一段需要先进行播放。").length / 2);
    playback.cancel();
  });

  it("aborts in-flight requests and stops scheduled sources on cancel", async () => {
    const { stops } = audioContextMock();
    let signal: AbortSignal | undefined;
    const playback = new PcmStreamPlayback(
      async (_text, requestSignal) => {
        signal = requestSignal;
        return new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(pcm(1000, 28800)); } });
      },
      () => 1, vi.fn(), vi.fn(),
    );
    playback.enqueue("这是一段会被取消的语音。");
    playback.finish();
    await vi.waitFor(() => expect(stops).toHaveLength(1));
    playback.cancel();
    expect(signal?.aborted).toBe(true);
    expect(stops[0]).toHaveBeenCalled();
  });

  it("buffers short packets, absorbs jitter and re-buffers after a real underrun", async () => {
    const { starts, scheduleTimes, clock } = audioContextMock();
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const playback = new PcmStreamPlayback(async () => new ReadableStream({
      start(value) { controller = value; },
    }), () => 1, vi.fn(), vi.fn());
    playback.enqueue("网络有波动时也应连续播放。"); playback.finish();
    await vi.waitFor(() => expect(controller).toBeDefined());
    controller.enqueue(pcm(1000, 7200));
    await Promise.resolve(); await Promise.resolve();
    expect(starts).toHaveLength(0);
    controller.enqueue(pcm(1000, 7200));
    await vi.waitFor(() => expect(starts).toHaveLength(1));
    // 250ms of network delay is absorbed by the reserve, with no timeline gap.
    clock().currentTime += 0.25;
    controller.enqueue(pcm(2000, 2400));
    await vi.waitFor(() => expect(starts).toHaveLength(2));
    expect(scheduleTimes[1]).toBeCloseTo(scheduleTimes[0] + 0.6);
    // A longer stall drains the reserve. Do not play each tiny packet in isolation.
    clock().currentTime = scheduleTimes[1] + 0.6;
    controller.enqueue(pcm(3000, 2400));
    await Promise.resolve(); await Promise.resolve();
    expect(starts).toHaveLength(2);
    controller.enqueue(pcm(3000, 19200));
    await vi.waitFor(() => expect(starts).toHaveLength(3));
    controller.enqueue(pcm(4000, 2400)); controller.close();
    await vi.waitFor(() => expect(starts).toHaveLength(4));
    expect(scheduleTimes[3]).toBeCloseTo(scheduleTimes[2] + 0.9);
    playback.cancel();
  });

  it("preserves already scheduled but unheard sentences when a later request fails", async () => {
    audioContextMock();
    const error = vi.fn();
    let rejectNext: (error: Error) => void = () => undefined;
    const playback = new PcmStreamPlayback((text) => text.startsWith("第一")
      ? Promise.resolve(stream(pcm(1000)))
      : new Promise((_resolve, reject) => { rejectNext = reject; }), () => 1, error, vi.fn());
    playback.enqueue("第一句还没播到。"); playback.enqueue("第二句请求失败。"); playback.finish();
    await vi.waitFor(() => expect(playback.progress()).toBe(0));
    await new Promise(resolve => setTimeout(resolve, 10));
    rejectNext(new Error("network"));
    await vi.waitFor(() => expect(error).toHaveBeenCalled());
    expect(error.mock.calls[0][0]).toEqual(["第一句还没播到。", "第二句请求失败。"]);
  });
});
