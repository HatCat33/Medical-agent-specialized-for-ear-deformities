type Emit = (text: string) => void;

/** Smooths irregular provider chunks without changing the persisted final response. */
export class ProgressiveTextStream {
  private pending: string[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private finishing = false;
  private settled = false;
  private started = false;
  private resolveFinished: () => void = () => undefined;
  private readonly finished: Promise<void>;
  private readonly onAbort = () => this.cancel();

  constructor(
    private emit: Emit,
    private signal: AbortSignal,
    private intervalMs = 55,
    private initialDelayMs = 180,
  ) {
    this.finished = new Promise(resolve => { this.resolveFinished = resolve; });
    if (signal.aborted) this.cancel();
    else signal.addEventListener("abort", this.onAbort, { once: true });
  }

  push(text: string) {
    if (this.settled || !text) return;
    this.pending.push(...Array.from(text));
    this.schedule();
  }

  finish(): Promise<void> {
    this.finishing = true;
    if (!this.pending.length) this.settle();
    else this.schedule();
    return this.finished;
  }

  cancel() {
    this.pending = [];
    this.settle();
  }

  private schedule() {
    if (this.timer !== null || this.settled || !this.pending.length) return;
    const delay = this.started ? this.intervalMs : this.initialDelayMs;
    this.timer = globalThis.setTimeout(() => {
      this.timer = null;
      if (this.settled) return;
      this.started = true;
      // Never catch up by dumping several characters. A delayed timer should make
      // the response slower, not create a visible burst on the next frame.
      const character = this.pending.shift()!;
      this.emit(character);
      if (this.pending.length) this.schedule();
      else if (this.finishing) this.settle();
    }, delay);
  }

  private settle() {
    if (this.settled) return;
    this.settled = true;
    if (this.timer !== null) globalThis.clearTimeout(this.timer);
    this.timer = null;
    this.signal.removeEventListener("abort", this.onAbort);
    this.resolveFinished();
  }
}
