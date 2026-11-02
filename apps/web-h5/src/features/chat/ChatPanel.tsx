import {
  AlertCircle,
  Camera,
  ImagePlus,
  Keyboard,
  LoaderCircle,
  Menu,
  Mic,
  Sparkles,
  SquarePen,
  Send,
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

import type { Message, SelectedImage } from "../../lib/types";
import { chooseEnterpriseWeChatImages, isEnterpriseWeChat } from "../../lib/wecomImagePicker";
import { CameraCapture } from "./CameraCapture";
import { MobileMediaPicker } from "./MobileMediaPicker";
import { VoiceActivity } from "./VoiceActivity";
import { MessageBubble } from "./MessageBubble";

interface ChatPanelProps {
  onCreateConversation: () => void;
  canCreateConversation: boolean;
  autoSpeech: boolean;
  playbackSupported: boolean;
  playbackNotice: string;
  onToggleSpeech: () => void;
  onReadMessage: (content: string) => void;
  title: string;
  messages: Message[];
  value: string;
  selectedImages: SelectedImage[];
  processingImages: boolean;
  sending: boolean;
  syncing: boolean;
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
  onImagePickerUnavailable: (reason?: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  onOpenHistory: () => void;
  onSuggestion: (value: string) => void;
}

interface DirectFilePickerWindow extends Window {
  showOpenFilePicker?: (options: {
    multiple?: boolean;
    types?: Array<{ description: string; accept: Record<string, string[]> }>;
  }) => Promise<Array<{ getFile: () => Promise<File> }>>;
}

function directFilePicker(): DirectFilePickerWindow["showOpenFilePicker"] {
  if (!window.isSecureContext) return undefined;
  return (window as DirectFilePickerWindow).showOpenFilePicker;
}

const suggestions = [
  { title: "术后活动", question: "在进行耳部功能锻炼时，需要注意什么？" },
  { title: "出院护理", question: "出院后必须要用纱布盖住耳朵吗？" },
  { title: "听力检查", question: "单侧小耳畸形是否影响听力，需要定期进行听力检查吗？" },
];

export function ChatPanel({
  onCreateConversation,
  canCreateConversation,
  autoSpeech,
  playbackSupported,
  playbackNotice,
  onToggleSpeech,
  onReadMessage,
  title,
  messages,
  value,
  selectedImages,
  processingImages,
  sending,
  syncing,
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
  onImagePickerUnavailable,
  onSubmit,
  onStop,
  onOpenHistory,
  onSuggestion,
}: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputId = useId();
  const galleryInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const shouldStickToBottom = useRef(true);
  const touchScrollStartY = useRef<number | null>(null);
  const holdStartY = useRef<number | null>(null);
  const cancelGesture = useRef(false);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [mediaPickerOpen, setMediaPickerOpen] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [showCancelHint, setShowCancelHint] = useState(false);
  const [composerExpanded, setComposerExpanded] = useState(false);

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (!shouldStickToBottom.current) return;
      const element = scrollRef.current;
      if (!element) return;
      const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
      if (distance > 1) element.scrollTop = element.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, selectedImages, syncing]);

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

  function openMediaPicker() {
    const mobile = window.matchMedia("(max-width: 720px), (pointer: coarse)").matches;
    if (mobile && (isEnterpriseWeChat() || directFilePicker())) setMediaPickerOpen(true);
    else if (mobile) galleryInputRef.current?.click();
    else galleryInputRef.current?.click();
  }

  async function chooseCamera() {
    setMediaPickerOpen(false);
    if (isEnterpriseWeChat()) {
      try {
        const files = await chooseEnterpriseWeChatImages(Math.max(1, 4 - selectedImages.length), "camera");
        if (files.length) void onSelectImages(files);
        return;
      } catch (error) {
        console.warn("wecom_camera_unavailable", error);
        onImagePickerUnavailable(error instanceof Error ? error.message : String(error));
      }
    }
    cameraInputRef.current?.click();
  }

  async function chooseGallery() {
    setMediaPickerOpen(false);
    if (isEnterpriseWeChat()) {
      try {
        const files = await chooseEnterpriseWeChatImages(Math.max(1, 4 - selectedImages.length), "album");
        if (files.length) void onSelectImages(files);
        return;
      } catch (error) {
        console.warn("wecom_gallery_unavailable", error);
        onImagePickerUnavailable(error instanceof Error ? error.message : String(error));
        galleryInputRef.current?.click();
        return;
      }
    }
    const picker = directFilePicker();
    if (picker) {
      try {
        const handles = await picker({
          multiple: true,
          types: [{
            description: "图片",
            accept: {
              "image/jpeg": [".jpg", ".jpeg"],
              "image/png": [".png"],
              "image/webp": [".webp"],
              "image/heic": [".heic", ".heif"],
            },
          }],
        });
        const files = await Promise.all(handles.map((handle) => handle.getFile()));
        if (files.length) void onSelectImages(files.slice(0, Math.max(0, 4 - selectedImages.length)));
        return;
      } catch (pickerError) {
        if (pickerError instanceof DOMException && pickerError.name === "AbortError") return;
      }
    }
    galleryInputRef.current?.click();
  }

  function beginHold(event?: PointerEvent<HTMLButtonElement> | KeyboardEvent<HTMLButtonElement>) {
    if (sending || syncing || listening || transcribing) return;
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
        >
          <strong>{title}</strong>
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
        onWheel={(event) => {
          if (event.deltaY < 0) shouldStickToBottom.current = false;
        }}
        onTouchStart={(event) => {
          touchScrollStartY.current = event.touches[0]?.clientY ?? null;
        }}
        onTouchMove={(event) => {
          const currentY = event.touches[0]?.clientY;
          if (currentY !== undefined && touchScrollStartY.current !== null
            && currentY - touchScrollStartY.current > 5) {
            shouldStickToBottom.current = false;
          }
        }}
        onTouchEnd={() => { touchScrollStartY.current = null; }}
      >
        {messages.length === 0 ? (
          <div className="welcome-block">
            <div className="welcome-identity">
              <div className="assistant-avatar" aria-hidden="true">
                <Sparkles size={21} />
              </div>
              <div className="welcome-name-row">
                <h1>耳畸形科普助手</h1>
                <span className="beta-badge">Beta 测试版</span>
              </div>
            </div>
            <div className="welcome-intro">
              <strong>您好！</strong>
              <span>可以询问耳畸形相关的一般知识、就诊准备与日常护理。</span>
            </div>
            <div className="suggestion-heading">常见问题</div>
            <div className="suggestion-list">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion.question}
                  type="button"
                  onClick={() => {
                    setVoiceMode(false);
                    onSuggestion(suggestion.question);
                  }}
                >
                  <strong>{suggestion.title}</strong>
                  <span>{suggestion.question}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="messages">
            {messages.map((message) => (
              <MessageBubble key={`${message.conversation_id}:${message.sequence_no}`} message={message}
                animate={message.id.startsWith("local-") || message.status === "streaming"}
                onRead={onReadMessage} />
            ))}
            {syncing && (
              <div className="conversation-sync" role="status" aria-live="polite">
                <LoaderCircle className="spin" size={18} />
                <div>
                  <strong>正在同步上一条回答</strong>
                  <span>可以先输入，完成后再发送</span>
                </div>
              </div>
            )}
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
              ref={galleryInputRef}
              id={`${inputId}-gallery`}
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
            <input
              ref={cameraInputRef}
              id={`${inputId}-camera`}
              className="visually-hidden image-input"
              type="file"
              accept="image/*"
              capture="environment"
              aria-hidden="true"
              tabIndex={-1}
              onChange={(event) => {
                if (event.currentTarget.files) void onSelectImages(event.currentTarget.files);
                event.currentTarget.value = "";
              }}
              disabled={sending || listening || transcribing || processingImages}
            />
            <div className="media-picker">
              <button
                type="button"
                className={`composer-tool-button ${
                  sending ||
                  listening ||
                  transcribing ||
                  processingImages ||
                  selectedImages.length >= 4
                    ? "is-disabled"
                    : ""
                }`}
                onClick={openMediaPicker}
                disabled={sending || listening || transcribing || processingImages || selectedImages.length >= 4}
                title={processingImages ? "正在处理图片" : "添加图片"}
                aria-label="添加图片"
              >
                {processingImages ? (
                  <LoaderCircle className="spin" size={19} />
                ) : (
                  <ImagePlus size={19} />
                )}
                {selectedImages.length > 0 && <span className="media-count" aria-hidden="true">{selectedImages.length}</span>}
              </button>
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
                disabled={sending || syncing || transcribing}
                aria-label="按住说话，松开发送"
              >
                {transcribing ? (
                  <>
                    <span className="hold-to-talk-icon"><LoaderCircle className="spin" size={17} /></span>
                    <span>识别中</span>
                  </>
                ) : listening ? (
                  <>
                    <span className="hold-to-talk-icon"><span className="recording-dot" aria-hidden="true" /></span>
                    <span>{showCancelHint ? "松开取消" : recordingReady ? "松开发送" : "开启麦克风中"}</span>
                  </>
                ) : (
                  <>
                    <span className="hold-to-talk-icon"><Mic size={17} /></span>
                    <span>{syncing ? "同步回答中" : sending ? "正在回答" : "按住说话"}</span>
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
                placeholder={syncing ? "正在同步上一条回答" : sending ? "回答生成中，可随时停止" : "输入问题，或添加图片"}
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
                  syncing ||
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
      <MobileMediaPicker
        open={mediaPickerOpen}
        remaining={Math.max(0, 4 - selectedImages.length)}
        onCamera={() => void chooseCamera()}
        onGallery={() => void chooseGallery()}
        onClose={() => setMediaPickerOpen(false)}
      />
    </section>
  );
}
