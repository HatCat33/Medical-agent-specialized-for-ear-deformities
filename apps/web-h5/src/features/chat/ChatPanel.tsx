import {
  AlertCircle,
  Camera,
  ImageOff,
  ImagePlus,
  Keyboard,
  LoaderCircle,
  Menu,
  Mic,
  SquarePen,
  Send,
  Sparkles,
  Square,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "../../lib/api";
import { patientVisibleMarkdown } from "../../lib/markdown";
import type { MediaAttachment, Message, SelectedImage } from "../../lib/types";
import { CameraCapture } from "./CameraCapture";
import { VoiceActivity } from "./VoiceActivity";

interface ChatPanelProps {
  onCreateConversation: () => void;
  canCreateConversation: boolean;
  autoSpeech: boolean;
  playbackSupported: boolean;
  playbackNotice: string;
  onToggleSpeech: () => void;
  title: string;
  messages: Message[];
  value: string;
  selectedImages: SelectedImage[];
  processingImages: boolean;
  sending: boolean;
  listening: boolean;
  recordingReady: boolean;
  audioLevel: number;
  transcribing: boolean;
  speechSupported: boolean;
  error: string | null;
  onValueChange: (value: string) => void;
  onSelectImages: (files: FileList | File[]) => void;
  onRemoveImage: (imageId: string) => void;
  onStartRecording: () => void;
  onReleaseRecording: () => void;
  onCancelRecording: () => void;
  onRecordingUnavailable: () => void;
  onCameraUnavailable: () => void;
  onSubmit: () => void;
  onStop: () => void;
  onOpenHistory: () => void;
  onSuggestion: (value: string) => void;
}

const suggestions = [
  "小耳畸形通常有哪些类型？",
  "第一次就诊前需要准备什么？",
  "日常护理有哪些注意事项？",
];

function SecureMessageImage({ attachment, index }: { attachment: MediaAttachment; index: number }) {
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    void api
      .mediaBlob(attachment.id)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment.id]);

  if (failed) {
    return (
      <div className="message-image-failed" title="图片加载失败">
        <ImageOff size={19} />
      </div>
    );
  }
  if (!source) return <div className="message-image-loading" aria-label="图片加载中" />;
  return (
    <a href={source} target="_blank" rel="noreferrer" title="查看原图">
      <img src={source} alt={`用户上传的第 ${index + 1} 张图片`} />
    </a>
  );
}

function MessageAttachments({ attachments }: { attachments: MediaAttachment[] }) {
  const images = attachments.filter((item) => item.kind === "image");
  if (!images.length) return null;
  return (
    <div className={`message-images count-${Math.min(images.length, 4)}`}>
      {images.map((attachment, index) => (
        <SecureMessageImage key={attachment.id} attachment={attachment} index={index} />
      ))}
    </div>
  );
}

export function ChatPanel({
  onCreateConversation,
  canCreateConversation,
  autoSpeech,
  playbackSupported,
  playbackNotice,
  onToggleSpeech,
  title,
  messages,
  value,
  selectedImages,
  processingImages,
  sending,
  listening,
  recordingReady,
  audioLevel,
  transcribing,
  speechSupported,
  error,
  onValueChange,
  onSelectImages,
  onRemoveImage,
  onStartRecording,
  onReleaseRecording,
  onCancelRecording,
  onRecordingUnavailable,
  onCameraUnavailable,
  onSubmit,
  onStop,
  onOpenHistory,
  onSuggestion,
}: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputId = useId();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldStickToBottom = useRef(true);
  const holdStartY = useRef<number | null>(null);
  const cancelGesture = useRef(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [showCancelHint, setShowCancelHint] = useState(false);
  const [composerExpanded, setComposerExpanded] = useState(false);

  useEffect(() => {
    if (!shouldStickToBottom.current) return;
    const frame = requestAnimationFrame(() => {
      const element = scrollRef.current;
      if (element) element.scrollTop = element.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, sending, selectedImages]);

  useEffect(() => {
    const element = textareaRef.current;
    if (!element) return;
    element.style.height = "auto";
    const styles = window.getComputedStyle(element);
    const minHeight = Number.parseFloat(styles.minHeight) || 42;
    const maxHeight = Number.parseFloat(styles.maxHeight) || 124;
    const contentHeight = element.scrollHeight;
    element.style.height = `${Math.min(Math.max(contentHeight, minHeight), maxHeight)}px`;
    element.style.overflowY = contentHeight > maxHeight ? "auto" : "hidden";
    setComposerExpanded(value.length > 0 && contentHeight > minHeight + 1);
  }, [value]);

  useEffect(() => {
    if (sending) shouldStickToBottom.current = true;
  }, [sending]);

  useEffect(
    () => () => {
      window.speechSynthesis?.cancel();
    },
    [],
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  function openDesktopCamera() {
    if (!window.isSecureContext || typeof navigator.mediaDevices?.getUserMedia !== "function") {
      onCameraUnavailable();
      return;
    }
    setCameraOpen(true);
  }

  function beginHold(event?: PointerEvent<HTMLButtonElement> | KeyboardEvent<HTMLButtonElement>) {
    if (sending || listening || transcribing) return;
    if (!speechSupported) {
      onRecordingUnavailable();
      return;
    }
    if (event && "pointerId" in event) {
      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      holdStartY.current = event.clientY;
    }
    cancelGesture.current = false;
    setShowCancelHint(false);
    onStartRecording();
  }

  function moveHold(event: PointerEvent<HTMLButtonElement>) {
    if (!listening || holdStartY.current === null) return;
    const shouldCancel = holdStartY.current - event.clientY > 56;
    cancelGesture.current = shouldCancel;
    setShowCancelHint(shouldCancel);
  }

  function endHold() {
    if (!listening) return;
    if (cancelGesture.current) onCancelRecording();
    else onReleaseRecording();
    holdStartY.current = null;
    cancelGesture.current = false;
    setShowCancelHint(false);
  }

  function cancelHold() {
    if (listening) onCancelRecording();
    holdStartY.current = null;
    cancelGesture.current = false;
    setShowCancelHint(false);
  }

  return (
    <section className="chat-shell">
      <header className="chat-header">
        <div className="chat-header-leading">
          <button
            className="icon-button history-trigger"
            type="button"
            onClick={onOpenHistory}
            title="历史会话"
            aria-label="打开历史会话"
          >
            <Menu size={23} />
          </button>
          <button
            className="icon-button new-conversation-trigger"
            type="button"
            onClick={onCreateConversation}
            disabled={!canCreateConversation}
            title="新建会话"
            aria-label="新建会话"
          >
            <SquarePen size={22} />
          </button>
        </div>

        <div
          className="chat-heading"
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
          }}
        >
          <strong style={{
            width: "100%",
            maxWidth: "min(90%, 420px)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            textAlign: "center"
          }}>
            {title}
          </strong>
          <span>内容仅供健康科普</span>
        </div>

        <button className={`icon-button auto-speech-toggle ${autoSpeech ? "is-enabled" : ""}`} type="button"
          aria-label={autoSpeech ? "关闭自动播报" : "开启自动播报"} aria-pressed={autoSpeech}
          disabled={!playbackSupported} onClick={onToggleSpeech}
          title={!playbackSupported ? "当前浏览器不支持声音播报" : autoSpeech ? "关闭自动播报" : "开启自动播报"}>
          {autoSpeech ? <Volume2 size={21} /> : <VolumeX size={21} />}
        </button>
      </header>

      <div
        className="message-scroll"
        ref={scrollRef}
        aria-live="polite"
        onScroll={(event) => {
          const element = event.currentTarget;
          shouldStickToBottom.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 96;
        }}
      >
        {messages.length === 0 ? (
          <div className="welcome-block">
            <div className="assistant-avatar" aria-hidden="true">
              <Sparkles size={24} />
            </div>
            <h1>你好，我是耳畸形科普助手</h1>
            <p>可以询问耳畸形相关的一般知识、就诊准备与护理常识。</p>
            <div className="suggestion-list">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  style={{
                    display: "inline-flex",
                    justifyContent: "center",
                    alignItems: "center",
                    textAlign: "center"
                  }}
                  onClick={() => {
                    setVoiceMode(false);
                    onSuggestion(suggestion);
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="messages">
            {messages.map((message) => (
              <article className={`message-row ${message.role}`} key={message.id}>
                <div
                  className={`message-content ${
                    (message.attachments?.length ?? 0) > 0 ? "has-attachments" : ""
                  }`}
                >
                  {message.status === "streaming" && !message.content ? (
                    <div className="typing" aria-label="正在生成回答">
                      <i />
                      <i />
                      <i />
                    </div>
                  ) : message.role === "assistant" ? (
                    <div
                      className={`message-body ${
                        message.status === "streaming" ? "is-streaming" : ""
                      }`}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {patientVisibleMarkdown(message.content)}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div className="user-message-bubble">
                      <MessageAttachments attachments={message.attachments ?? []} />
                      <p>{message.content}</p>
                    </div>
                  )}
                  {message.status === "interrupted" && (
                    <small className="message-status">回答已中断</small>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <footer className="composer-area">
        {(listening || transcribing) && <VoiceActivity listening={listening} ready={recordingReady} transcribing={transcribing} cancelling={showCancelHint} level={audioLevel} />}
        {playbackNotice && <div className="playback-notice" role="status">{playbackNotice}</div>}
        {error && (
          <div className="error-banner" role="alert">
            <AlertCircle size={16} />
            {error}
          </div>
        )}
        <form className="composer" onSubmit={submit}>
          {selectedImages.length > 0 && (
            <div className="selected-images">
              {selectedImages.map((image) => (
                <div className="selected-image" key={image.id}>
                  <img src={image.previewUrl} alt={image.file.name} />
                  <button
                    type="button"
                    onClick={() => onRemoveImage(image.id)}
                    disabled={sending || listening || transcribing || processingImages}
                    title="移除图片"
                    aria-label={`移除图片 ${image.file.name}`}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
              {/* <span className="media-retention-note">图片随会话保存，删除会话时一并删除</span> */}
            </div>
          )}
          <div
            className={`composer-row ${voiceMode ? "is-voice-mode" : ""} ${
              sending ? "is-sending" : ""
            } ${!voiceMode && composerExpanded ? "is-expanded" : ""}`}
          >
            <input
              id={`${inputId}-images`}
              className="visually-hidden image-input"
              type="file"
              accept="image/*,.heic,.heif"
              multiple
              aria-hidden="true"
              tabIndex={-1}
              onChange={(event) => {
                if (event.currentTarget.files) void onSelectImages(event.currentTarget.files);
                event.currentTarget.value = "";
              }}
              disabled={sending || listening || transcribing || processingImages}
            />
            <div className="media-picker">
              <label
                className={`composer-tool-button ${
                  sending ||
                  listening ||
                  transcribing ||
                  processingImages ||
                  selectedImages.length >= 4
                    ? "is-disabled"
                    : ""
                }`}
                htmlFor={`${inputId}-images`}
                role="button"
                tabIndex={
                  sending ||
                  listening ||
                  transcribing ||
                  processingImages ||
                  selectedImages.length >= 4
                    ? -1
                    : 0
                }
                title={processingImages ? "正在处理图片" : "添加图片"}
                aria-label="添加图片"
                aria-disabled={
                  sending ||
                  listening ||
                  transcribing ||
                  processingImages ||
                  selectedImages.length >= 4
                }
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    document.getElementById(`${inputId}-images`)?.click();
                  }
                }}
              >
                {processingImages ? (
                  <LoaderCircle className="spin" size={19} />
                ) : (
                  <ImagePlus size={19} />
                )}
              </label>
            </div>
            <button
              className="composer-tool-button desktop-camera-button"
              type="button"
              onClick={openDesktopCamera}
              disabled={
                sending ||
                listening ||
                transcribing ||
                processingImages ||
                selectedImages.length >= 4
              }
              title="使用电脑摄像头拍照"
              aria-label="使用电脑摄像头拍照"
            >
              <Camera size={19} />
            </button>
            {voiceMode ? (
              <button
                className={`hold-to-talk ${listening ? "is-recording" : ""} ${
                  showCancelHint ? "is-cancelling" : ""
                }`}
                type="button"
                onPointerDown={beginHold}
                onPointerMove={moveHold}
                onPointerUp={endHold}
                onPointerCancel={cancelHold}
                onContextMenu={(event) => event.preventDefault()}
                onKeyDown={(event) => {
                  if ((event.key === " " || event.key === "Enter") && !event.repeat) {
                    event.preventDefault();
                    beginHold(event);
                  }
                }}
                onKeyUp={(event) => {
                  if (event.key === " " || event.key === "Enter") {
                    event.preventDefault();
                    endHold();
                  }
                }}
                disabled={sending || transcribing}
                aria-label="按住说话，松开发送"
              >
                {transcribing ? (
                  <>
                    <LoaderCircle className="spin" size={18} />
                    识别中
                  </>
                ) : listening ? (
                  <>
                    <span className="recording-dot" aria-hidden="true" />
                    {showCancelHint ? "松开取消" : recordingReady ? "松开发送" : "开启麦克风中"}
                  </>
                ) : (
                  <>
                    <Mic size={18} />
                    {sending ? "正在回答" : "按住说话"}
                  </>
                )}
              </button>
            ) : (
              <textarea
                ref={textareaRef}
                value={value}
                onChange={(event) => onValueChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    onSubmit();
                  }
                }}
                placeholder={sending ? "回答生成中，可随时停止" : "输入问题，或添加图片"}
                aria-label="咨询问题"
                rows={1}
                maxLength={4000}
                disabled={sending || transcribing}
              />
            )}
            <div className="composer-mode-switch">
              <button
                className={`composer-tool-button ${voiceMode ? "is-active" : ""}`}
                type="button"
                onClick={() => {
                  if (!voiceMode && !speechSupported) {
                    onRecordingUnavailable();
                    return;
                  }
                  setVoiceMode((current) => !current);
                }}
                disabled={sending || listening || transcribing}
                title={
                  speechSupported
                    ? voiceMode
                      ? "切换到键盘输入"
                      : "切换到按住说话"
                    : "录音需要 HTTPS 或 localhost"
                }
                aria-label={voiceMode ? "切换到键盘输入" : "切换到按住说话"}
              >
                {voiceMode ? <Keyboard size={19} /> : <Mic size={19} />}
              </button>
            </div>
            {sending ? (
              <button
                className="send-button stop-button"
                type="button"
                onClick={onStop}
                title="停止生成"
                aria-label="停止生成"
              >
                <Square size={16} fill="currentColor" />
              </button>
            ) : !voiceMode ? (
              <button
                className="send-button"
                type="submit"
                disabled={
                  transcribing ||
                  processingImages ||
                  voiceMode ||
                  (value.trim().length === 0 && selectedImages.length === 0)
                }
                title="发送"
                aria-label="发送问题"
              >
                <Send size={19} />
              </button>
            ) : null}
          </div>
        </form>

      </footer>
      {cameraOpen && (
        <CameraCapture
          onCapture={(file) => void onSelectImages([file])}
          onClose={() => setCameraOpen(false)}
        />
      )}
    </section>
  );
}
