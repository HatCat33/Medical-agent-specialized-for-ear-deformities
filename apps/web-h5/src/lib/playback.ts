import { patientVisibleMarkdown } from "./markdown";
import { CloudPlayback } from "./cloudPlayback";
import { PcmStreamPlayback, type StreamSynthesize } from "./pcmStreamPlayback";

export interface SpeechSettings {
  auto_play: boolean; voice: string; language: string; rate: number; pitch: number; volume: number;
  provider?: "browser" | "openai-compatible"; cloud_voice?: string;
  streaming_enabled?: boolean; stream_format?: "pcm_s16le";
  stream_sample_rate?: number; stream_channels?: number;
}

export const defaultSpeechSettings: SpeechSettings = {
  auto_play: true, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1,
};

const FIRST_STREAM_MIN_CHARS = 12;
const FIRST_STREAM_MAX_CHARS = 40;
const FIRST_STREAM_DELAY_MS = 360;
const STREAM_MIN_CHARS = 28;
const STREAM_MAX_CHARS = 112;

export function speechText(markdown: string): string {
  return patientVisibleMarkdown(markdown)
    .replace(/```[\s\S]*?```/g, "")
    .replace(/!\[[^\]]*]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)]\([^)]*\)/g, "$1")
    .replace(/\[\d+(?:\s*[-,，]\s*\d+)*\]/g, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*>+]\s+/gm, "")
    .replace(/[*_`~|]/g, "")
    .trim();
}

/** One live response at a time. History and persisted message IDs never enter this queue. */
export class AnswerPlayback {
  settings = defaultSpeechSettings;
  enabled = true;
  private buffer = "";
  private fullText = "";
  private consumed = 0;
  private generation = 0;
  private queue: string[] = [];
  private active: SpeechSynthesisUtterance | null = null;
  private browserStartTimer: ReturnType<typeof setTimeout> | null = null;
  private partialFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private retrying = false;
  private failedVoices = new Set<string>();
  private cloudUnavailable = false;
  private streamUnavailable = false;
  private answerFinished = false;
  private manualPlayback = false;
  private cloud: CloudPlayback | null;
  private stream: PcmStreamPlayback | null;
  private spokenCharacters = 0;
  private sessionId: string | null = null;

  constructor(
    private onError: (usingDeviceFallback?: boolean, reason?: unknown) => void,
    synthesize?: (text: string, signal: AbortSignal) => Promise<Blob>,
    private onStart: () => void = () => undefined,
    streamSynthesize?: StreamSynthesize,
  ) {
    this.cloud = synthesize ? new CloudPlayback(synthesize, () => this.settings.volume, (remaining, reason) => {
      this.cloudUnavailable = true;
      this.queue.unshift(...remaining);
      this.onError(true, reason);
      this.next();
    }, this.onStart) : null;
    this.stream = streamSynthesize ? new PcmStreamPlayback(
      streamSynthesize,
      () => this.settings.volume,
      (remaining, reason) => {
        console.warn("answer_speech_stream_fallback", reason);
        this.streamUnavailable = true;
        this.queue.unshift(...remaining);
        this.next();
        if (this.answerFinished) this.cloud?.finish();
      },
      this.onStart,
    ) : null;
  }

  configure(settings: SpeechSettings) {
    this.settings = settings;
    this.stream?.configure({
      sampleRate: settings.stream_sample_rate ?? 24000,
      channels: settings.stream_channels ?? 1,
    });
  }

  private clearBrowserStartTimer() {
    if (this.browserStartTimer !== null) globalThis.clearTimeout(this.browserStartTimer);
    this.browserStartTimer = null;
  }

  private clearPartialFlushTimer() {
    if (this.partialFlushTimer !== null) globalThis.clearTimeout(this.partialFlushTimer);
    this.partialFlushTimer = null;
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (!enabled) this.mute();
  }

  begin(manualPlayback = false, sessionId?: string) {
    this.cancel();
    this.sessionId = sessionId ?? null;
    this.manualPlayback = manualPlayback;
    this.cloudUnavailable = false;
    this.streamUnavailable = false;
    this.answerFinished = false;
    this.spokenCharacters = 0;
    this.failedVoices.clear();
    this.onStart();
    this.unlock();
  }

  unlock() {
    window.speechSynthesis?.resume();
    this.stream?.unlock();
    if (this.isEnabled()) this.cloud?.unlock();
  }

  read(text: string) {
    this.begin(true);
    this.append(text);
    this.finish();
  }

  cancel(sessionId?: string) {
    if (sessionId !== undefined && sessionId !== this.sessionId) return;
    this.generation += 1;
    this.clearBrowserStartTimer();
    this.clearPartialFlushTimer();
    this.retrying = false;
    this.manualPlayback = false;
    this.cloud?.cancel();
    this.stream?.cancel();
    this.buffer = ""; this.fullText = ""; this.consumed = 0; this.queue = []; this.active = null;
    this.sessionId = null;
    window.speechSynthesis?.cancel();
  }

  mute() {
    this.enabled = false;
    this.manualPlayback = false;
    this.clearBrowserStartTimer();
    this.clearPartialFlushTimer();
    this.retrying = false;
    this.buffer = ""; this.consumed = this.fullText.length;
    this.queue = []; this.active = null; this.generation += 1;
    this.cloud?.cancel();
    this.stream?.cancel();
    window.speechSynthesis?.cancel();
  }

  append(delta: string, sessionId?: string) {
    if (sessionId !== undefined && sessionId !== this.sessionId) return;
    this.fullText += delta;
    const visible = patientVisibleMarkdown(this.fullText);
    if (!this.isEnabled()) { this.consumed = visible.length; this.buffer = ""; return; }
    this.buffer = visible.slice(this.consumed);
    this.flush(false);
    this.schedulePartialFlush();
  }

  finish(sessionId?: string) {
    if (sessionId !== undefined && sessionId !== this.sessionId) return;
    this.answerFinished = true;
    this.clearPartialFlushTimer();
    this.flush(true);
    if (this.isEnabled() && this.settings.provider === "openai-compatible") {
      if (this.useStreaming()) this.stream?.finish();
      else this.cloud?.finish();
    }
  }

  private schedulePartialFlush() {
    const streaming = this.useStreaming();
    const firstStreamingSegment = streaming && this.spokenCharacters === 0;
    const minimum = firstStreamingSegment ? FIRST_STREAM_MIN_CHARS : streaming ? STREAM_MIN_CHARS : 12;
    if (!this.isEnabled() || this.partialFlushTimer !== null || this.buffer.length < minimum) return;
    const generation = this.generation;
    this.partialFlushTimer = globalThis.setTimeout(() => {
      this.partialFlushTimer = null;
      if (generation !== this.generation || !this.isEnabled() || this.buffer.length < minimum) return;
      if (streaming) this.flushStreaming(false);
      else {
        this.emitFromBuffer(Math.min(this.buffer.length, 36));
        this.next();
      }
      this.schedulePartialFlush();
    }, firstStreamingSegment ? FIRST_STREAM_DELAY_MS : streaming ? 450 : 180);
  }

  private emitFromBuffer(length: number) {
    const chunk = this.buffer.slice(0, length);
    this.consumed += length;
    this.buffer = this.buffer.slice(length);
    const text = speechText(chunk);
    if (this.isEnabled() && text) {
      this.spokenCharacters += Array.from(text).length;
      this.queue.push(text);
      this.next();
    }
  }

  private flush(final: boolean) {
    if (this.useStreaming()) { this.flushStreaming(final); return; }
    // Start at a natural clause boundary so speech follows the stream instead of waiting
    // for a long paragraph. Forced boundaries keep punctuation-free answers responsive.
    while (this.buffer) {
      const hardBoundary = /[。！？!?；;\n]/.exec(this.buffer);
      const softBoundary = /[，,：:、]/g;
      let softLength = 0;
      for (const match of this.buffer.matchAll(softBoundary)) {
        const candidate = (match.index ?? 0) + 1;
        if (candidate >= 14) { softLength = candidate; break; }
      }
      let length = 0;
      if (softLength && softLength <= 56) length = softLength;
      else if (hardBoundary && hardBoundary.index < 64) length = hardBoundary.index + 1;
      else if (this.buffer.length >= 56) length = 56;
      else if (hardBoundary) length = hardBoundary.index + 1;
      else if (final) length = this.buffer.length;
      if (!length) break;
      this.emitFromBuffer(length);
    }
    this.next();
  }

  private flushStreaming(final: boolean) {
    while (this.buffer) {
      const first = this.spokenCharacters === 0;
      const minimum = first ? FIRST_STREAM_MIN_CHARS : STREAM_MIN_CHARS;
      const maximum = first ? FIRST_STREAM_MAX_CHARS : STREAM_MAX_CHARS;
      const hardBoundary = [...this.buffer.matchAll(/[。！？!?；;\n]/g)]
        .map(match => (match.index ?? 0) + 1)
        .find(length => length >= 2 && length <= maximum);
      const softBoundary = [...this.buffer.matchAll(/[，,：:、]/g)]
        .map(match => (match.index ?? 0) + 1)
        .find(length => length >= minimum && length <= maximum);
      const naturalBoundary = [hardBoundary, softBoundary]
        .filter((length): length is number => length !== undefined)
        .sort((left, right) => left - right)[0];
      // Prefer a complete clause. Only punctuation-free text that grows very long is
      // force-cut; this avoids splitting names and medical terms at an arbitrary 8 chars.
      const length = naturalBoundary
        ?? (this.buffer.length >= maximum ? maximum : final ? this.buffer.length : 0);
      if (!length) break;
      this.emitFromBuffer(length);
    }
    this.next();
  }

  private next() {
    if (this.useStreaming()) {
      for (const text of this.queue.splice(0)) this.stream?.enqueue(text);
      return;
    }
    if (this.settings.provider === "openai-compatible" && !this.cloudUnavailable) {
      for (const text of this.queue.splice(0)) this.cloud?.enqueue(text);
      return;
    }
    const synth = window.speechSynthesis;
    if (!synth || this.active || this.retrying || !this.isEnabled()) return;
    let text = this.queue.shift();
    if (!text) return;
    // Short utterances avoid mobile speech engines stalling on a large queued paragraph.
    while (this.queue.length && text.length + this.queue[0].length <= 80) text += this.queue.shift()!;
    this.speakBrowser(text, this.generation, true);
  }

  private useStreaming() {
    return this.settings.provider === "openai-compatible"
      && this.settings.streaming_enabled === true
      && this.stream?.supported === true
      && !this.streamUnavailable;
  }

  private isEnabled() {
    return this.enabled || this.manualPlayback;
  }

  private speakBrowser(text: string, generation: number, useConfiguredVoice: boolean) {
    const synth = window.speechSynthesis;
    if (!synth || generation !== this.generation || !this.isEnabled()) return;
    const utterance = new SpeechSynthesisUtterance(text);
    const { voice, language, rate, pitch, volume } = this.settings;
    const voices = synth.getVoices().filter(item => !this.failedVoices.has(item.voiceURI));
    const configured = voices.find(item => !this.failedVoices.has(item.voiceURI) && voice && (item.voiceURI === voice || item.name === voice));
    const sameLanguage = voices.filter(item => item.lang.toLowerCase() === language.toLowerCase());
    const sameBaseLanguage = voices.filter(item => item.lang.split("-")[0].toLowerCase() === language.split("-")[0].toLowerCase());
    const fallback = sameLanguage.find(item => item.localService && item !== configured)
      ?? sameBaseLanguage.find(item => item.localService && item !== configured)
      ?? sameLanguage[0] ?? sameBaseLanguage[0] ?? voices.find(item => item.default) ?? null;
    const selected = useConfiguredVoice ? configured ?? fallback : fallback;
    utterance.voice = selected;
    utterance.lang = selected?.lang ?? language; utterance.rate = rate; utterance.pitch = pitch; utterance.volume = volume;
    const canRetryLocally = useConfiguredVoice && configured != null && fallback != null && fallback.localService && configured !== fallback;
    const retryOrFail = () => {
      if (generation !== this.generation || this.active !== utterance) return;
      this.clearBrowserStartTimer();
      this.active = null;
      if (selected) this.failedVoices.add(selected.voiceURI);
      synth.cancel();
      if (canRetryLocally) {
        this.retrying = true;
        globalThis.setTimeout(() => {
          if (generation !== this.generation) return;
          this.retrying = false; this.speakBrowser(text, generation, false);
        }, 80);
      } else {
        this.queue = [];
        this.onError();
      }
    };
    utterance.onstart = () => {
      if (generation === this.generation && this.active === utterance) {
        this.clearBrowserStartTimer();
        this.onStart();
      }
    };
    utterance.onend = () => {
      if (generation === this.generation && this.active === utterance) {
        this.clearBrowserStartTimer(); this.active = null; this.next();
      }
    };
    utterance.onerror = event => {
      if (generation !== this.generation || this.active !== utterance) return;
      if (event.error === "canceled" || event.error === "interrupted") return;
      retryOrFail();
    };
    this.active = utterance;
    this.clearBrowserStartTimer();
    const mobileWebView = typeof navigator !== "undefined"
      && /Android|iPhone|iPad|MicroMessenger|wxwork/i.test(navigator.userAgent);
    this.browserStartTimer = globalThis.setTimeout(retryOrFail, mobileWebView ? 7000 : 5000);
    try { synth.resume(); synth.speak(utterance); }
    catch { retryOrFail(); }
  }

}
