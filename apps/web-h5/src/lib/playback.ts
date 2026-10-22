import { patientVisibleMarkdown } from "./markdown";
import { CloudPlayback } from "./cloudPlayback";

export interface SpeechSettings {
  auto_play: boolean; voice: string; language: string; rate: number; pitch: number; volume: number;
  provider?: "browser" | "openai-compatible"; cloud_voice?: string;
}

export const defaultSpeechSettings: SpeechSettings = {
  auto_play: true, voice: "", language: "zh-CN", rate: 1, pitch: 1, volume: 1,
};

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
  private retrying = false;
  private failedVoices = new Set<string>();
  private cloud: CloudPlayback | null;

  constructor(private onError: () => void, synthesize?: (text: string, signal: AbortSignal) => Promise<Blob>) {
    this.cloud = synthesize ? new CloudPlayback(synthesize, () => this.settings.volume, onError) : null;
  }

  configure(settings: SpeechSettings) { this.settings = settings; }

  private clearBrowserStartTimer() {
    if (this.browserStartTimer !== null) globalThis.clearTimeout(this.browserStartTimer);
    this.browserStartTimer = null;
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (!enabled) this.mute();
  }

  begin() {
    this.cancel();
    window.speechSynthesis?.resume();
  }

  cancel() {
    this.generation += 1;
    this.clearBrowserStartTimer();
    this.retrying = false;
    this.cloud?.cancel();
    this.buffer = ""; this.fullText = ""; this.consumed = 0; this.queue = []; this.active = null;
    window.speechSynthesis?.cancel();
  }

  mute() {
    this.enabled = false;
    this.clearBrowserStartTimer();
    this.retrying = false;
    this.buffer = ""; this.consumed = this.fullText.length;
    this.queue = []; this.active = null; this.generation += 1;
    this.cloud?.cancel();
    window.speechSynthesis?.cancel();
  }

  append(delta: string) {
    this.fullText += delta;
    const visible = patientVisibleMarkdown(this.fullText);
    if (!this.enabled) { this.consumed = visible.length; this.buffer = ""; return; }
    this.buffer = visible.slice(this.consumed);
    this.flush(false);
  }

  finish() {
    this.flush(true);
    if (this.enabled && this.settings.provider === "openai-compatible") this.cloud?.finish();
  }

  private flush(final: boolean) {
    // Retain short incomplete lines so Markdown markers and reference headings aren't spoken.
    while (this.buffer) {
      const boundary = /[。！？!?；;\n]/.exec(this.buffer);
      const length = boundary ? Math.min(boundary.index + 1, 160) : this.buffer.length >= 160 ? 160 : final ? this.buffer.length : 0;
      if (!length) break;
      const chunk = this.buffer.slice(0, length);
      this.consumed += length; this.buffer = this.buffer.slice(length);
      const text = speechText(chunk);
      if (this.enabled && text) this.queue.push(text);
    }
    this.next();
  }

  private next() {
    if (this.settings.provider === "openai-compatible") {
      for (const text of this.queue.splice(0)) this.cloud?.enqueue(text);
      return;
    }
    const synth = window.speechSynthesis;
    if (!synth || this.active || this.retrying || !this.enabled) return;
    let text = this.queue.shift();
    if (!text) return;
    // The first live sentence can start promptly; buffered sentences share one utterance.
    while (this.queue.length && text.length + this.queue[0].length <= 240) text += this.queue.shift()!;
    this.speakBrowser(text, this.generation, true);
  }

  private speakBrowser(text: string, generation: number, useConfiguredVoice: boolean) {
    const synth = window.speechSynthesis;
    if (!synth || generation !== this.generation || !this.enabled) return;
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
      if (generation === this.generation && this.active === utterance) this.clearBrowserStartTimer();
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
    this.browserStartTimer = globalThis.setTimeout(retryOrFail, 4000);
    try { synth.resume(); synth.speak(utterance); }
    catch { retryOrFail(); }
  }

}
