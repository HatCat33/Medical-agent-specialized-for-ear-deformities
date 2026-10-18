export interface AudioRecordingController {
  stop: () => Promise<File | null>;
  cancel: () => void;
}

const MIME_TYPE_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

function preferredMimeType(): string | undefined {
  return MIME_TYPE_CANDIDATES.find((item) => MediaRecorder.isTypeSupported(item));
}

function extensionForMimeType(mimeType: string): string {
  if (mimeType.startsWith("audio/mp4")) return "m4a";
  if (mimeType.startsWith("audio/ogg")) return "ogg";
  return "webm";
}

export function audioRecordingSupported(): boolean {
  return (
    window.isSecureContext &&
    typeof MediaRecorder !== "undefined" &&
    typeof navigator.mediaDevices?.getUserMedia === "function"
  );
}

export async function startAudioRecording(): Promise<AudioRecordingController> {
  if (!audioRecordingSupported()) throw new Error("recording_not_supported");

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
    video: false,
  });
  const requestedMimeType = preferredMimeType();
  let recorder: MediaRecorder;
  try {
    recorder = new MediaRecorder(
      stream,
      requestedMimeType ? { mimeType: requestedMimeType } : undefined,
    );
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw error;
  }

  const chunks: Blob[] = [];
  let cancelled = false;
  let settled = false;
  let resolveStopped: (file: File | null) => void = () => undefined;
  const stopped = new Promise<File | null>((resolve) => {
    resolveStopped = resolve;
  });

  function releaseStream() {
    stream.getTracks().forEach((track) => track.stop());
  }

  function settle(file: File | null) {
    if (settled) return;
    settled = true;
    releaseStream();
    resolveStopped(file);
  }

  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  recorder.onerror = () => settle(null);
  recorder.onstop = () => {
    if (cancelled || chunks.length === 0) {
      settle(null);
      return;
    }
    const mimeType = recorder.mimeType || requestedMimeType || "audio/webm";
    const blob = new Blob(chunks, { type: mimeType });
    settle(
      new File([blob], `recording-${Date.now()}.${extensionForMimeType(mimeType)}`, {
        type: mimeType,
        lastModified: Date.now(),
      }),
    );
  };

  try {
    recorder.start(250);
  } catch (error) {
    releaseStream();
    throw error;
  }

  return {
    stop: async () => {
      if (recorder.state !== "inactive") recorder.stop();
      return stopped;
    },
    cancel: () => {
      cancelled = true;
      if (recorder.state !== "inactive") recorder.stop();
      else settle(null);
    },
  };
}

export function readableRecordingError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "无法使用麦克风，请检查浏览器权限。";
    if (error.name === "NotFoundError") return "没有检测到可用的麦克风。";
    if (error.name === "NotReadableError") return "麦克风正被其他应用占用。";
  }
  if (error instanceof Error && error.message === "recording_not_supported") {
    return "当前环境不支持录音，请使用 HTTPS 或 localhost 打开。";
  }
  return "录音失败，请重试。";
}
