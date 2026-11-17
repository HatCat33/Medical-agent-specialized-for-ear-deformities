type Synthesize = (text: string, signal: AbortSignal) => Promise<Blob>;
interface Segment {
  controller: AbortController;
  state: "fetching" | "ready" | "playing" | "failed";
  text: string;
  url?: string;
  error?: unknown;
}

const SILENT_AUDIO =
  "data:audio/wav;base64,UklGRiwAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQgAAACAgICAgICAgA==";

const MAX_FETCHING = 2;
const MAX_BUFFERED_SEGMENTS = 5;
const RETRY_DELAY_MS = 280;

/** Bounded look-ahead for short clips. The provider never receives a recombined paragraph. */
export class CloudPlayback {
  private pending: string[] = [];
  private segments: Segment[] = [];
  private player: HTMLAudioElement;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;
  private finished = false;
  private failed = false;
  private dispatched = false;
  private unlocked = false;
  private unlockPromise: Promise<void> | null = null;

  constructor(
    private synthesize: Synthesize,
    private volume: () => number,
    private onError: (remaining: string[], reason: unknown) => void,
    private onStart: () => void,
  ) {
    // Reusing the element that was unlocked by a tap is required by iOS Safari.
    // Creating a new Audio element after an async TTS request is often blocked.
    this.player = new Audio();
    this.player.preload = "auto";
    this.player.setAttribute("playsinline", "");
    this.player.setAttribute("webkit-playsinline", "");
  }

  unlock() {
    if (this.unlocked || this.unlockPromise) return;
    this.player.onended = null;
    this.player.onerror = null;
    this.player.src = SILENT_AUDIO;
    // The sample itself is silent. Keeping the element unmuted works more reliably in WebViews.
    this.player.volume = 1;
    this.player.load();
    try {
      const attempt = this.player.play();
      this.unlockPromise = Promise.resolve(attempt)
        .then(() => {
          this.unlocked = true;
          this.player.pause();
          this.player.currentTime = 0;
        })
        .catch(() => undefined)
        .finally(() => {
          this.unlockPromise = null;
          this.playReady();
        });
    } catch {
      this.unlockPromise = null;
    }
  }

  enqueue(text: string) {
    if (this.failed) return;
    this.pending.push(text);
    const pendingCharacters = this.pending.reduce((total, item) => total + item.length, 0);
    // Very short greetings are briefly collected; a useful first clause starts immediately.
    if (!this.dispatched && pendingCharacters >= 12) this.fill();
    else if (this.dispatched) this.fill();
    if (this.pending.length && this.timer === null) {
      this.timer = setTimeout(() => {
        this.timer = null; this.fill(true);
      }, 90);
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
    this.player.onended = null;
    this.player.onerror = null;
    this.player.onplaying = null;
    if (!this.unlockPromise) {
      this.player.pause();
      this.player.removeAttribute("src");
      this.player.load();
    }
    for (const segment of this.segments) this.release(segment);
    this.segments = []; this.pending = [];
    this.finished = false; this.failed = false; this.dispatched = false;
  }

  private release(segment: Segment) {
    segment.controller.abort();
    if (segment.url) URL.revokeObjectURL(segment.url);
  }

  private fill(force = false) {
    if (this.failed || this.segments.some(segment => segment.state === "failed")) return;
    while (this.pending.length && this.segments.length < MAX_BUFFERED_SEGMENTS
      && this.segments.filter(segment => segment.state === "fetching").length < MAX_FETCHING) {
      if (!force && !this.dispatched
        && this.pending.reduce((total, item) => total + item.length, 0) < 12) break;
      let text = this.pending.shift()!;
      // Never leave a tiny trailing word as a standalone utterance.
      while (this.pending.length && (text.length < 8 || this.pending[0].length < 8)
        && text.length + this.pending[0].length <= 64) {
        text += this.pending.shift()!;
      }
      this.dispatched = true;
      const segment: Segment = { controller: new AbortController(), state: "fetching", text };
      this.segments.push(segment);
      void this.prepare(segment, text, this.generation);
    }
    this.playReady();
  }

  private async prepare(segment: Segment, text: string, generation: number) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const blob = await this.synthesize(text, segment.controller.signal);
        if (generation !== this.generation || segment.controller.signal.aborted) return;
        segment.url = URL.createObjectURL(blob);
        segment.state = "ready";
        this.fill(true);
        this.playReady();
        return;
      } catch (error) {
        if (generation !== this.generation || segment.controller.signal.aborted) return;
        segment.error = error;
        if (attempt === 0 && shouldRetrySynthesis(error)) {
          await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS));
          if (generation !== this.generation || segment.controller.signal.aborted) return;
          continue;
        }
      }
    }
    segment.state = "failed";
    this.playReady();
  }

  private playReady() {
    const segment = this.segments[0];
    if (!segment || this.failed) return;
    if (segment.state === "failed") { this.fail(segment.error); return; }
    if (segment.state !== "ready") return;
    if (this.unlockPromise) {
      void this.unlockPromise.finally(() => this.playReady());
      return;
    }
    segment.state = "playing";
    const generation = this.generation;
    this.player.onended = () => {
      if (generation !== this.generation || this.segments[0] !== segment) return;
      this.segments.shift();
      this.release(segment);
      this.fill(true);
      this.playReady();
    };
    this.player.onerror = () => {
      if (generation !== this.generation || this.segments[0] !== segment) return;
      segment.state = "failed";
      this.fail(new Error("audio_decode_failed"));
    };
    this.player.onplaying = () => {
      if (generation === this.generation && this.segments[0] === segment) {
        this.unlocked = true;
        this.onStart();
      }
    };
    this.player.src = segment.url!;
    this.player.volume = this.volume();
    this.player.load();
    try {
      void this.player.play().catch(() => {
        if (generation === this.generation && this.segments[0] === segment) {
          this.fail(new Error("audio_play_blocked"));
        }
      });
    } catch (error) { this.fail(error); }
  }

  private fail(reason: unknown) {
    const remaining = [
      ...this.segments.map(segment => segment.text),
      ...this.pending,
    ];
    this.cancel();
    this.failed = true;
    this.onError(remaining, reason);
  }
}

function shouldRetrySynthesis(error: unknown): boolean {
  if (!(error instanceof Error)) return true;
  const status = /speech_synthesis_failed:(\d{3}):/.exec(error.message)?.[1];
  if (!status) return true;
  const code = Number(status);
  return code === 429 || code === 502 || code === 503;
}
