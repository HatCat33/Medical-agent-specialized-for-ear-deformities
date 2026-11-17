import { afterEach, describe, expect, it, vi } from "vitest";

import { AnswerPlayback, defaultSpeechSettings, speechText } from "./playback";

function setup(voices = [{ name: "中文", voiceURI: "zh-test", lang: "zh-CN", localService: true }]) {
  const utterances: SpeechSynthesisUtterance[] = [];
  const cancel = vi.fn();
  vi.stubGlobal("window", { speechSynthesis: {
    cancel, resume: vi.fn(), getVoices: () => voices,
    speak: (utterance: SpeechSynthesisUtterance) => utterances.push(utterance),
  } });
  vi.stubGlobal("SpeechSynthesisUtterance", class { constructor(public text: string) {} });
  const onError = vi.fn();
  return { playback: new AnswerPlayback(onError), utterances, cancel, onError };
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("live answer speech", () => {
  it("cloud synthesis uses a cancellable queue and never plays a response after mute", async () => {
    setup();
    const play = vi.fn().mockResolvedValue(undefined);
    const pause = vi.fn();
    vi.stubGlobal("Audio", class {
      src = ""; currentTime = 0; preload = ""; volume = 1;
      play = play; pause = pause; load = vi.fn(); removeAttribute = vi.fn(); setAttribute = vi.fn();
    });
    let resolveAudio: (blob: Blob) => void = () => undefined;
    let signal: AbortSignal | undefined;
    const synthesize = vi.fn((_text: string, requestSignal: AbortSignal) => {
      signal = requestSignal;
      return new Promise<Blob>(resolve => { resolveAudio = resolve; });
    });
    const playback = new AnswerPlayback(vi.fn(), synthesize);
    playback.configure({ ...defaultSpeechSettings, provider: "openai-compatible", cloud_voice: "custom" });
    playback.begin(); playback.append("第一句。第二句。");
    playback.finish();
    expect(synthesize).toHaveBeenCalledTimes(1);
    playback.setEnabled(false);
    expect(signal?.aborted).toBe(true);
    resolveAudio(new Blob(["audio"]));
    await Promise.resolve(); await Promise.resolve();
    expect(play).toHaveBeenCalledTimes(1); // Only the silent user-gesture unlock played.
    expect(synthesize).toHaveBeenCalledTimes(1);
  });
  it("speaks completed sentences before generation ends and flushes the tail once", () => {
    const { playback, utterances } = setup();
    playback.configure({ ...defaultSpeechSettings, voice: "zh-test", rate: 1.2, pitch: 0.8, volume: 0.7 });
    playback.begin(); playback.append("第一句");
    expect(utterances).toHaveLength(0);
    playback.append("。第二句。");
    expect(utterances.map(item => item.text)).toEqual(["第一句。"]);
    expect(utterances[0].voice?.voiceURI).toBe("zh-test");
    expect(utterances[0].rate).toBe(1.2);
    playback.append("没有标点的尾句"); playback.finish(); playback.finish();
    utterances[0].onend?.(new Event("end") as SpeechSynthesisEvent);
    expect(utterances.map(item => item.text)).toEqual(["第一句。", "第二句。没有标点的尾句"]);
  });

  it("starts a long streaming sentence at a natural clause before the final period", () => {
    const { playback, utterances } = setup();
    playback.begin();
    playback.append("孩子术后需要保持局部清洁干燥，并注意观察有没有红肿渗液，后续再按医生安排复诊。");
    expect(utterances[0].text).toBe("孩子术后需要保持局部清洁干燥，");
  });

  it("starts punctuation-free streaming text after a short latency", () => {
    vi.useFakeTimers();
    const { playback, utterances } = setup();
    playback.begin();
    playback.append("这是一段暂时没有标点但已经足够开始播放的模型回答内容");
    expect(utterances).toHaveLength(0);
    vi.advanceTimersByTime(180);
    expect(utterances[0].text).toBe("这是一段暂时没有标点但已经足够开始播放的模型回答内容");
    playback.cancel();
  });

  it("mute cancels immediately, clears pending audio, and doesn't replay muted sentences", () => {
    const { playback, utterances, cancel } = setup();
    playback.begin(); playback.append("第一句。"); playback.append("第二句。");
    const staleEnd = utterances[0].onend;
    playback.setEnabled(false);
    playback.append("第三句。");
    playback.setEnabled(true);
    staleEnd?.call(utterances[0], new Event("end") as SpeechSynthesisEvent);
    playback.append("第四句。");
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(utterances.map(item => item.text)).toEqual(["第一句。", "第四句。"]);
  });

  it("removes references and Markdown formatting from spoken text", () => {
    const { playback, utterances } = setup();
    playback.begin();
    playback.append("**请遵医嘱**[1]。\n\n参考资料：\n- [1] 内部医院资料"); playback.finish();
    expect(utterances.map(item => item.text)).toEqual(["请遵医嘱。"]);
    expect(speechText("### 标题\n[门诊](https://example.test) **说明**")).toBe("标题\n门诊 说明");
  });

  it("doesn't read incomplete text accumulated while muted", () => {
    const { playback, utterances } = setup();
    playback.begin(); playback.setEnabled(false); playback.append("静音期间的半句话");
    playback.setEnabled(true); playback.append("现在开始出声。");
    expect(utterances[0].text).toBe("现在开始出声。");
  });

  it("allows an explicit message read while automatic playback is muted", () => {
    const { playback, utterances } = setup();
    playback.setEnabled(false);
    playback.read("手动朗读这条消息。");
    expect(utterances.map(item => item.text)).toEqual(["手动朗读这条消息。"]);
    playback.cancel();
  });

  it("discarding a response prevents old callbacks from advancing its queue", () => {
    const { playback, utterances } = setup();
    playback.begin(); playback.append("旧回答。旧的下一句。");
    playback.cancel();
    utterances[0].onend?.(new Event("end") as SpeechSynthesisEvent);
    expect(utterances).toHaveLength(1);
    playback.begin(); playback.append("新回答。");
    expect(utterances[1].text).toBe("新回答。");
  });

  it("ignores late speech callbacks from an older conversation turn", () => {
    const { playback, utterances } = setup();
    playback.begin(false, "old-turn");
    playback.append("旧回答。", "old-turn");

    playback.begin(false, "new-turn");
    playback.cancel("old-turn");
    playback.append("不应播出的旧内容。", "old-turn");
    playback.finish("old-turn");
    playback.append("新会话回答。", "new-turn");
    playback.finish("new-turn");

    expect(utterances.map(item => item.text)).toEqual([
      "旧回答。",
      "新会话回答。",
    ]);
  });

  it("falls back to a local language voice when a configured online voice does not start", () => {
    vi.useFakeTimers();
    const { playback, utterances, onError } = setup([
      { name: "在线粤语", voiceURI: "remote-zh", lang: "zh-HK", localService: false },
      { name: "本机中文", voiceURI: "local-zh", lang: "zh-CN", localService: true },
    ]);
    playback.configure({ ...defaultSpeechSettings, voice: "remote-zh", language: "zh-HK" });
    playback.begin(); playback.append("需要回退的回答。");
    expect(utterances[0].voice?.voiceURI).toBe("remote-zh");
    vi.advanceTimersByTime(5080);
    expect(utterances[1].voice?.voiceURI).toBe("local-zh");
    expect(onError).not.toHaveBeenCalled();
    playback.cancel();
  });

  it("keeps sentence order during a failed local voice retry and avoids that voice afterwards", () => {
    vi.useFakeTimers();
    const { playback, utterances } = setup([
      { name: "坏音色", voiceURI: "bad", lang: "zh-CN", localService: true },
      { name: "可用音色", voiceURI: "good", lang: "zh-CN", localService: true },
    ]);
    playback.configure({ ...defaultSpeechSettings, voice: "bad" });
    playback.begin(); playback.append("第一句。");
    utterances[0].onerror?.({ error: "synthesis-failed" } as SpeechSynthesisErrorEvent);
    playback.append("第二句。");
    expect(utterances).toHaveLength(1);
    vi.advanceTimersByTime(80);
    expect(utterances[1].voice?.voiceURI).toBe("good");
    expect(utterances[1].text).toBe("第一句。");
    utterances[1].onend?.(new Event("end") as SpeechSynthesisEvent);
    expect(utterances[2].text).toBe("第二句。");
    expect(utterances[2].voice?.voiceURI).toBe("good");
    playback.cancel();
  });
});
