export type StreamSynthesize = (
  text: string,
  signal: AbortSignal,
) => Promise<ReadableStream<Uint8Array>>;

interface Segment {
  controller: AbortController;
  state: "fetching" | "ready" | "streaming" | "failed";
  text: string;
  stream?: ReadableStream<Uint8Array>;
  error?: unknown;
  reader?: ReadableStreamDefaultReader<Uint8Array>;
}

interface PlayedSegment {
  segment: Segment;
  characters: number;
  start: number;
  end: number;
  complete: boolean;
}

export interface PcmStreamConfiguration {
  sampleRate: number;
  channels: number;
}

const MAX_FETCHING = 3;
const MAX_BUFFERED_SEGMENTS = 4;
const SEGMENT_COLLECTION_MS = 80;
// Start the short lead-in quickly; later packets and segments still build a reserve.
const INITIAL_BUFFER_SECONDS = 0.06;
const STARTUP_AUDIO_SECONDS = 0.32;
const RECOVERY_AUDIO_SECONDS = 0.8;
const MAX_AHEAD_SECONDS = 12;
const INITIAL_TEXT_CHARACTERS = 12;

type AudioContextConstructor = new (options?: AudioContextOptions) => AudioContext;

function audioContextConstructor(): AudioContextConstructor | undefined {
  if (typeof window === "undefined") return undefined;
  return window.AudioContext
    ?? (window as typeof window & { webkitAudioContext?: AudioContextConstructor }).webkitAudioContext;
}

/** Converts arbitrary network chunks of signed 16-bit little-endian PCM into samples. */
export class Pcm16Decoder {
  private carry = new Uint8Array(0);

  decode(chunk: Uint8Array, channels: number, final = false): Float32Array {
    const combined = new Uint8Array(this.carry.length + chunk.length);
    combined.set(this.carry);
    combined.set(chunk, this.carry.length);
    const bytesPerFrame = Math.max(1, channels) * 2;
    const usable = final ? combined.length - (combined.length % bytesPerFrame)
      : Math.floor(combined.length / bytesPerFrame) * bytesPerFrame;
    this.carry = combined.slice(usable);
    if (final && this.carry.length) throw new Error("pcm_frame_truncated");
    const output = new Float32Array(usable / 2);
    const view = new DataView(combined.buffer, combined.byteOffset, usable);
    for (let index = 0; index < output.length; index += 1) {
      const value = view.getInt16(index * 2, true);
      output[index] = value < 0 ? value / 32768 : value / 32767;
    }
    return output;
  }

  reset() { this.carry = new Uint8Array(0); }
}

/** Schedules raw PCM chunks on one Web Audio timeline, preserving text-segment order. */
export class PcmStreamPlayback {
  readonly supported: boolean;
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private pending: string[] = [];
  private segments: Segment[] = [];
  private sources = new Set<AudioBufferSourceNode>();
  private generation = 0;
  private nextStartTime = 0;
  private failed = false;
  private dispatched = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private refillTimer: ReturnType<typeof setTimeout> | null = null;
  private played: PlayedSegment[] = [];
  private configuration: PcmStreamConfiguration = { sampleRate: 24000, channels: 1 };

  constructor(
    private synthesize: StreamSynthesize,
    private volume: () => number,
    private onError: (remaining: string[], reason: unknown) => void,
    private onStart: () => void,
  ) {
    const Context = audioContextConstructor();
    this.supported = false;
    if (Context) {
      try {
        this.context = new Context({ latencyHint: "interactive" });
        this.gain = this.context.createGain();
        this.gain.connect(this.context.destination);
        this.supported = true;
      } catch {
        this.context = null;
        this.gain = null;
      }
    }
  }

  configure(configuration: PcmStreamConfiguration) {
    this.configuration = configuration;
  }

  unlock() {
    if (!this.context || this.context.state === "closed") return;
    void this.context.resume().catch(() => undefined);
  }

  /** AudioContext time stops while suspended, unlike wall-clock estimates. */
  progress(): number {
    if (!this.context || this.context.state !== "running") return 0;
    const now = this.context.currentTime;
    let characters = 0;
    for (const item of this.played) {
      if (now <= item.start) break;
      const duration = item.complete ? item.end - item.start
        : Math.max(item.end - item.start, item.characters / 5.5);
      const fraction = Math.min(item.complete ? 1 : 0.9, (now - item.start) / Math.max(duration, 0.01));
      characters += item.characters * fraction;
      if (fraction < 1) break;
    }
    return characters;
  }

  enqueue(text: string) {
    if (this.failed || !this.supported) return;
    this.pending.push(text);
    if (this.pending.length && this.timer === null) {
      // Briefly collect adjacent clauses so a trailing name or one-character tail isn't
      // synthesized as a separate utterance. This delay is far below provider latency.
      this.timer = setTimeout(() => { this.timer = null; this.fill(true); }, SEGMENT_COLLECTION_MS);
    }
  }

  finish() {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.fill(true);
  }

  cancel() {
    this.generation += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    if (this.refillTimer !== null) clearTimeout(this.refillTimer);
    this.refillTimer = null;
    for (const segment of this.segments) {
      segment.controller.abort();
      void (segment.reader?.cancel() ?? segment.stream?.cancel())?.catch(() => undefined);
    }
    for (const source of this.sources) {
      try { source.stop(); } catch { /* The source may already have ended. */ }
    }
    this.sources.clear();
    this.pending = [];
    this.segments = [];
    this.nextStartTime = 0;
    this.played = [];
    this.failed = false;
    this.dispatched = false;
  }

  private fill(force = false) {
    if (this.failed) return;
    if (this.context && this.nextStartTime - this.context.currentTime > MAX_AHEAD_SECONDS) {
      if (this.refillTimer === null) this.refillTimer = setTimeout(() => {
        this.refillTimer = null; this.fill(true);
      }, 250);
      return;
    }
    while (this.pending.length && this.segments.length < MAX_BUFFERED_SEGMENTS
      && this.segments.filter(segment => segment.state !== "failed").length < MAX_FETCHING) {
      if (!force && !this.dispatched
        && this.pending.reduce((total, item) => total + item.length, 0) < INITIAL_TEXT_CHARACTERS) break;
      let text = this.pending.shift()!;
      while (this.pending.length && (text.length < 8 || this.pending[0].length < 8)
        && text.length + this.pending[0].length <= 112) {
        text += this.pending.shift()!;
      }
      this.dispatched = true;
      const segment: Segment = { controller: new AbortController(), state: "fetching", text };
      this.segments.push(segment);
      void this.prepare(segment, this.generation);
    }
  }

  private async prepare(segment: Segment, generation: number) {
    try {
      segment.stream = await this.synthesize(segment.text, segment.controller.signal);
      if (generation !== this.generation || segment.controller.signal.aborted) {
        await segment.stream.cancel();
        return;
      }
      segment.state = "ready";
      this.fill(true);
      this.consumeReady();
    } catch (error) {
      if (generation !== this.generation || segment.controller.signal.aborted) return;
      segment.error = error;
      segment.state = "failed";
      this.consumeReady();
    }
  }

  private consumeReady() {
    const segment = this.segments[0];
    if (!segment || this.failed) return;
    if (segment.state === "failed") { this.fail(segment.error); return; }
    if (segment.state !== "ready" || !segment.stream) return;
    segment.state = "streaming";
    void this.consume(segment, this.generation);
  }

  private async consume(segment: Segment, generation: number) {
    const context = this.context;
    if (!context || !this.gain) { this.fail(new Error("web_audio_unavailable")); return; }
    const decoder = new Pcm16Decoder();
    const reader = segment.stream!.getReader();
    segment.reader = reader;
    let pending = new Float32Array(0);
    let scheduled = false;
    let bytes = 0;
    const timing: PlayedSegment = { segment, characters: Array.from(segment.text).length, start: 0, end: 0, complete: false };
    try {
      // A mobile WebView may leave resume() pending until a later user gesture. Audio nodes
      // can still be queued while suspended, so network consumption must not wait on it.
      void context.resume().catch(() => undefined);
      while (true) {
        const { done, value } = await reader.read();
        if (generation !== this.generation || segment.controller.signal.aborted) {
          await reader.cancel();
          return;
        }
        bytes += value?.byteLength ?? 0;
        if (bytes > 8 * 1024 * 1024) throw new Error("pcm_stream_too_large");
        const decoded = decoder.decode(value ?? new Uint8Array(0), this.configuration.channels, done);
        if (decoded.length) {
          const combined = new Float32Array(pending.length + decoded.length);
          combined.set(pending); combined.set(decoded, pending.length); pending = combined;
        }
        const frames = pending.length / this.configuration.channels;
        const starved = this.nextStartTime > 0 && this.nextStartTime <= context.currentTime + 0.025;
        const requiredSeconds = !this.nextStartTime ? STARTUP_AUDIO_SECONDS : starved ? RECOVERY_AUDIO_SECONDS : 0.08;
        if (frames >= this.configuration.sampleRate * requiredSeconds || (done && frames > 0)) {
          this.schedule(pending, !scheduled, timing);
          pending = new Float32Array(0);
          scheduled = true;
        }
        if (done) break;
      }
      if (!scheduled) throw new Error("empty_pcm_stream");
      timing.complete = true;
      if (generation !== this.generation || this.segments[0] !== segment) return;
      this.segments.shift();
      this.fill(true);
      this.consumeReady();
    } catch (error) {
      if (generation === this.generation && !segment.controller.signal.aborted) this.fail(error);
    } finally {
      segment.reader = undefined;
      reader.releaseLock();
    }
  }

  private schedule(samples: Float32Array, first: boolean, timing: PlayedSegment) {
    const context = this.context!;
    const { channels, sampleRate } = this.configuration;
    const frames = Math.floor(samples.length / channels);
    if (!frames) return;
    const buffer = context.createBuffer(channels, frames, sampleRate);
    for (let channel = 0; channel < channels; channel += 1) {
      const values = buffer.getChannelData(channel);
      for (let frame = 0; frame < frames; frame += 1) values[frame] = samples[frame * channels + channel];
    }
    const source = context.createBufferSource();
    source.buffer = buffer;
    this.gain!.gain.value = this.volume();
    source.connect(this.gain!);
    const recovering = this.nextStartTime > 0 && this.nextStartTime < context.currentTime;
    if (recovering) {
      // Timing only: never log patient text or audio. Helps distinguish network starvation.
      console.info("answer_speech_buffer_underrun", {
        gap_ms: Math.round((context.currentTime - this.nextStartTime) * 1000),
        buffered_ms: Math.round(buffer.duration * 1000),
      });
    }
    const minimumStart = context.currentTime + (!this.nextStartTime ? INITIAL_BUFFER_SECONDS : recovering ? 0.12 : 0.025);
    const start = Math.max(this.nextStartTime, minimumStart);
    if (first) {
      timing.start = start;
      this.played.push(timing);
    }
    source.start(start);
    this.nextStartTime = start + buffer.duration;
    timing.end = this.nextStartTime;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
    if (first) this.onStart();
  }

  private fail(reason: unknown) {
    // Scheduled audio hasn't necessarily been heard. Preserve those sentences on
    // fallback instead of silently losing them when cancel() stops their nodes.
    const unplayed = this.played.filter(item => item.end > (this.context?.currentTime ?? 0))
      .map(item => item.segment);
    const remaining = [...new Set([...unplayed, ...this.segments])].map(segment => segment.text);
    remaining.push(...this.pending);
    this.cancel();
    this.failed = true;
    this.onError(remaining, reason);
  }
}
