type Synthesize = (text: string, signal: AbortSignal) => Promise<Blob>;
interface Segment {
  controller: AbortController;
  state: "fetching" | "ready" | "playing" | "failed";
  audio?: HTMLAudioElement;
  url?: string;
}

/** Bounded look-ahead: two requests, at most three segments including the one playing. */
export class CloudPlayback {
  private pending: string[] = [];
  private segments: Segment[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;
  private finished = false;
  private failed = false;
  private dispatched = false;

  constructor(private synthesize: Synthesize, private volume: () => number, private onError: () => void) {}

  enqueue(text: string) {
    if (this.failed) return;
    this.pending.push(text);
    // Briefly collect short sentences so every punctuation mark doesn't require a new clip.
    if (!this.dispatched) {
      // A sentence is already a safe speech boundary: start synthesis immediately
      // instead of waiting for the complete model response or an arbitrary timer.
      this.fill(true);
      return;
    }
    if (this.pending.join("").length >= 240) this.fill();
    if (this.pending.length && this.timer === null) {
      this.timer = setTimeout(() => {
        this.timer = null; this.fill(true);
      }, 120);
    }
  }

  finish() {
    this.finished = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.fill(true);
  }

  cancel() {
    this.generation += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    for (const segment of this.segments) this.release(segment);
    this.segments = []; this.pending = [];
    this.finished = false; this.failed = false; this.dispatched = false;
  }

  private release(segment: Segment) {
    segment.controller.abort();
    if (segment.audio) {
      segment.audio.onended = null; segment.audio.onerror = null;
      segment.audio.pause();
      segment.audio.removeAttribute("src");
      segment.audio.load();
    }
    if (segment.url) URL.revokeObjectURL(segment.url);
  }

  private fill(force = false) {
    if (this.failed) return;
    while (this.pending.length && this.segments.length < 3
      && this.segments.filter(segment => segment.state === "fetching").length < 2) {
      if (!force && !this.finished && this.pending.join("").length < 240) break;
      let text = this.pending.shift()!;
      while (this.pending.length && text.length + this.pending[0].length <= 320) {
        text += this.pending.shift()!;
      }
      this.dispatched = true;
      const segment: Segment = { controller: new AbortController(), state: "fetching" };
      this.segments.push(segment);
      void this.prepare(segment, text, this.generation);
    }
    this.playReady();
  }

  private async prepare(segment: Segment, text: string, generation: number) {
    try {
      const blob = await this.synthesize(text, segment.controller.signal);
      if (generation !== this.generation || segment.controller.signal.aborted) return;
      segment.url = URL.createObjectURL(blob);
      const audio = new Audio(segment.url);
      segment.audio = audio;
      audio.preload = "auto"; audio.volume = this.volume();
      audio.onended = () => {
        if (generation !== this.generation || this.segments[0] !== segment) return;
        this.segments.shift(); this.release(segment);
        this.fill(this.finished);
      };
      audio.onerror = () => {
        if (generation !== this.generation || segment.controller.signal.aborted) return;
        segment.state = "failed"; this.playReady();
      };
      segment.state = "ready";
      audio.load(); // Fetch/decode the next clip while the previous one is still playing.
      this.fill(this.finished);
    } catch {
      if (generation !== this.generation || segment.controller.signal.aborted) return;
      segment.state = "failed"; this.playReady();
    }
  }

  private playReady() {
    const segment = this.segments[0];
    if (!segment || this.failed) return;
    if (segment.state === "failed") { this.fail(); return; }
    if (segment.state !== "ready" || !segment.audio) return;
    segment.state = "playing";
    const generation = this.generation;
    try {
      void segment.audio.play().catch(() => {
        if (generation === this.generation && this.segments[0] === segment) this.fail();
      });
    } catch { this.fail(); }
  }

  private fail() {
    this.cancel(); this.failed = true; this.onError();
  }
}
