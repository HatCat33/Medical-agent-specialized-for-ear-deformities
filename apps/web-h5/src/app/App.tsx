import { useCallback, useEffect, useRef, useState } from "react";

import { ChatPanel } from "../features/chat/ChatPanel";
import { HistorySidebar } from "../features/history/HistorySidebar";
import { api } from "../lib/api";
import { prepareImageForUpload } from "../lib/image";
import {
  audioRecordingSupported,
  readableRecordingError,
  startAudioRecording,
  type AudioRecordingController,
} from "../lib/speech";
import type {
  Conversation,
  InputMode,
  MediaAttachment,
  Message,
  SelectedImage,
} from "../lib/types";

const MAX_IMAGES = 4;
const MAX_RECORDING_MS = 60_000;
const IMAGE_ONLY_QUESTION = "请对上传图片中与耳部健康科普相关的可见信息进行客观说明。";
const SIDEBAR_COLLAPSED_KEY = "medical-agent-sidebar-collapsed";

interface SubmitOptions {
  content: string;
  inputMode: InputMode;
}

function readableTranscriptionError(error: unknown): string {
  if (error instanceof Error && error.message === "asr_not_configured") {
    return "语音识别模型尚未配置，请先设置 ASR 环境变量。";
  }
  if (error instanceof Error && error.message === "audio_type_not_supported") {
    return "当前浏览器的录音格式暂不支持。";
  }
  if (error instanceof Error && error.message === "audio_too_large") {
    return "录音文件过大，请缩短录音时间。";
  }
  return "语音识别服务暂时不可用，请重试或改用文字输入。";
}

export function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [selectedImages, setSelectedImages] = useState<SelectedImage[]>([]);
  const [processingImages, setProcessingImages] = useState(false);
  const [sending, setSending] = useState(false);
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true",
  );
  const streamController = useRef<AbortController | null>(null);
  const recordingController = useRef<AudioRecordingController | null>(null);
  const recordingAttempt = useRef(0);
  const recordingReleasePending = useRef(false);
  const recordingTimer = useRef<number | null>(null);
  const previewUrls = useRef(new Set<string>());

  const clearSelectedImages = useCallback(() => {
    setSelectedImages((current) => {
      current.forEach((item) => {
        URL.revokeObjectURL(item.previewUrl);
        previewUrls.current.delete(item.previewUrl);
      });
      return [];
    });
  }, []);

  const cancelRecording = useCallback(() => {
    recordingAttempt.current += 1;
    recordingReleasePending.current = false;
    if (recordingTimer.current !== null) window.clearTimeout(recordingTimer.current);
    recordingTimer.current = null;
    recordingController.current?.cancel();
    recordingController.current = null;
    setListening(false);
  }, []);

  useEffect(
    () => () => {
      recordingAttempt.current += 1;
      recordingController.current?.cancel();
      if (recordingTimer.current !== null) window.clearTimeout(recordingTimer.current);
      previewUrls.current.forEach((url) => URL.revokeObjectURL(url));
      window.speechSynthesis?.cancel();
    },
    [],
  );

  const refreshConversations = useCallback(async () => {
    const items = await api.listConversations();
    setConversations(items);
    return items;
  }, []);

  const openConversation = useCallback(
    async (conversationId: string) => {
      setError(null);
      setInput("");
      setInputMode("text");
      cancelRecording();
      clearSelectedImages();
      setActiveId(conversationId);
      setHistoryOpen(false);
      const detail = await api.getConversation(conversationId);
      setMessages(detail.messages);
    },
    [cancelRecording, clearSelectedImages],
  );

  useEffect(() => {
    void (async () => {
      try {
        const items = await refreshConversations();
        if (items[0]) await openConversation(items[0].id);
      } catch {
        setError("暂时无法连接服务，请稍后重试。");
      }
    })();
  }, [openConversation, refreshConversations]);

  async function createConversation(preserveComposer = false): Promise<Conversation> {
    if (!preserveComposer) {
      setInput("");
      setInputMode("text");
      cancelRecording();
      clearSelectedImages();
    }
    const conversation = await api.createConversation();
    setConversations((current) => [conversation, ...current]);
    setActiveId(conversation.id);
    setMessages([]);
    setHistoryOpen(false);
    return conversation;
  }

  async function deleteConversation(conversationId: string) {
    if (!window.confirm("确定删除这条会话、消息和上传的图片吗？")) return;
    try {
      await api.deleteConversation(conversationId);
      const remaining = conversations.filter((item) => item.id !== conversationId);
      setConversations(remaining);
      if (activeId === conversationId) {
        if (remaining[0]) await openConversation(remaining[0].id);
        else {
          setActiveId(null);
          setMessages([]);
          clearSelectedImages();
        }
      }
    } catch {
      setError("会话删除失败，请稍后重试。");
    }
  }

  async function selectImages(files: FileList | File[]) {
    setError(null);
    const incoming = Array.from(files);
    if (selectedImages.length + incoming.length > MAX_IMAGES) {
      setError(`每次最多上传 ${MAX_IMAGES} 张图片。`);
      return;
    }
    setProcessingImages(true);
    try {
      const prepared: File[] = [];
      for (const file of incoming) prepared.push(await prepareImageForUpload(file));
      const additions = prepared.map((file) => {
        const previewUrl = URL.createObjectURL(file);
        previewUrls.current.add(previewUrl);
        return { id: crypto.randomUUID(), file, previewUrl };
      });
      setSelectedImages((current) => [...current, ...additions]);
      setInputMode("multimodal");
    } catch (imageError) {
      const reason = imageError instanceof Error ? imageError.message : "";
      if (reason === "image_source_too_large") {
        setError("原始照片不能超过 25 MB，请降低相机分辨率后重试。");
      } else if (reason === "heic_conversion_failed") {
        setError("这张 HEIC/HEIF 照片暂时无法转换，请在相册中导出为 JPG 后重试。");
      } else if (reason === "image_too_large") {
        setError("照片压缩后仍然过大，请降低相机分辨率后重试。");
      } else if (reason === "image_conversion_failed") {
        setError("照片处理失败，请换一张图片重试。");
      } else {
        setError("仅支持 JPG、PNG、WebP、HEIC 和 HEIF 图片。");
      }
    } finally {
      setProcessingImages(false);
    }
  }

  function removeImage(imageId: string) {
    const willBeEmpty = selectedImages.length === 1 && selectedImages[0]?.id === imageId;
    setSelectedImages((current) => {
      const removed = current.find((item) => item.id === imageId);
      if (removed) {
        URL.revokeObjectURL(removed.previewUrl);
        previewUrls.current.delete(removed.previewUrl);
      }
      return current.filter((item) => item.id !== imageId);
    });
    if (willBeEmpty && inputMode === "multimodal") setInputMode("text");
  }

  async function startVoiceRecording() {
    if (sending || transcribing || listening) return;
    const attempt = recordingAttempt.current + 1;
    recordingAttempt.current = attempt;
    recordingReleasePending.current = false;
    setError(null);
    setListening(true);
    try {
      const controller = await startAudioRecording();
      if (recordingAttempt.current !== attempt) {
        controller.cancel();
        return;
      }
      recordingController.current = controller;
      recordingTimer.current = window.setTimeout(() => {
        void finishVoiceRecording(controller);
      }, MAX_RECORDING_MS);
      if (recordingReleasePending.current) await finishVoiceRecording(controller);
    } catch (recordingError) {
      if (recordingAttempt.current !== attempt) return;
      setListening(false);
      setError(readableRecordingError(recordingError));
    }
  }

  async function finishVoiceRecording(controller = recordingController.current) {
    if (!controller) {
      recordingReleasePending.current = true;
      return;
    }
    if (recordingController.current !== controller) return;
    recordingController.current = null;
    recordingReleasePending.current = false;
    if (recordingTimer.current !== null) window.clearTimeout(recordingTimer.current);
    recordingTimer.current = null;
    setListening(false);
    setTranscribing(true);
    setError(null);
    try {
      const file = await controller.stop();
      if (!file || file.size < 128) {
        setError("录音时间太短，请按住后再说话。");
        return;
      }
      const result = await api.transcribeAudio(file);
      const content = [input.trim(), result.text.trim()].filter(Boolean).join("\n");
      if (!content) {
        setError("没有识别到语音，请重试。");
        return;
      }
      await submitMessage({
        content,
        inputMode: selectedImages.length ? "multimodal" : "voice",
      });
    } catch (transcriptionError) {
      setError(readableTranscriptionError(transcriptionError));
    } finally {
      setTranscribing(false);
    }
  }

  function releaseVoiceRecording() {
    if (!listening) return;
    recordingReleasePending.current = true;
    void finishVoiceRecording();
  }

  async function submitMessage(options?: SubmitOptions) {
    const content = (options?.content ?? input).trim();
    const files = selectedImages.map((item) => item.file);
    if ((!content && !files.length) || sending || processingImages) return;
    cancelRecording();
    setSending(true);
    setError(null);
    const controller = new AbortController();
    streamController.current = controller;

    let uploaded: MediaAttachment[] = [];
    let userPersisted = false;
    let userTemporaryId = "";
    let assistantTemporaryId = "";
    try {
      if (files.length) uploaded = await api.uploadImages(files);
      const conversation = activeId
        ? conversations.find((item) => item.id === activeId) ?? (await createConversation(true))
        : await createConversation(true);
      const displayContent = content || IMAGE_ONLY_QUESTION;
      const now = new Date().toISOString();
      const sequence = (messages.at(-1)?.sequence_no ?? 0) + 1;
      userTemporaryId = `local-user-${Date.now()}`;
      assistantTemporaryId = `local-assistant-${Date.now()}`;
      const userMessage: Message = {
        id: userTemporaryId,
        conversation_id: conversation.id,
        sequence_no: sequence,
        role: "user",
        content: displayContent,
        status: "completed",
        safety_code: null,
        created_at: now,
        attachments: uploaded,
      };
      const assistantMessage: Message = {
        id: assistantTemporaryId,
        conversation_id: conversation.id,
        sequence_no: sequence + 1,
        role: "assistant",
        content: "",
        status: "streaming",
        safety_code: null,
        created_at: now,
        attachments: [],
      };
      setInput("");
      setMessages((current) => [...current, userMessage, assistantMessage]);
      setActiveId(conversation.id);

      await api.streamMessage(
        conversation.id,
        content,
        uploaded.map((item) => item.id),
        uploaded.length ? "multimodal" : (options?.inputMode ?? inputMode),
        {
          onUserMessage: (message) => {
            userPersisted = true;
            clearSelectedImages();
            setInputMode("text");
            setMessages((current) =>
              current.map((item) => (item.id === userTemporaryId ? message : item)),
            );
          },
          onDelta: (chunk) => {
            setMessages((current) =>
              current.map((item) =>
                item.id === assistantTemporaryId
                  ? { ...item, content: item.content + chunk }
                  : item,
              ),
            );
          },
          onDone: (message) => {
            setMessages((current) =>
              current.map((item) => (item.id === assistantTemporaryId ? message : item)),
            );
          },
        },
        controller.signal,
      );
    } catch (caught) {
      const stopped = caught instanceof DOMException && caught.name === "AbortError";
      if (!userPersisted) {
        setMessages((current) =>
          current.filter((item) => item.id !== userTemporaryId && item.id !== assistantTemporaryId),
        );
        setInput(content);
        await Promise.allSettled(uploaded.map((item) => api.deleteMedia(item.id)));
      } else {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== assistantTemporaryId) return item;
            return {
              ...item,
              content: item.content || (stopped ? "已停止生成。" : "回答生成失败，请稍后重试。"),
              status: "interrupted",
            };
          }),
        );
      }
      if (!stopped) setError("连接中断，已保留成功保存的内容，请稍后重试。");
    } finally {
      streamController.current = null;
      setSending(false);
      try {
        await refreshConversations();
      } catch {
        // The current message remains usable even if refreshing the sidebar fails.
      }
    }
  }

  function stopGeneration() {
    streamController.current?.abort();
  }

  const active = conversations.find((item) => item.id === activeId);

  return (
    <main className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
      <div
        className={`sidebar-backdrop ${historyOpen ? "is-open" : ""}`}
        onClick={() => setHistoryOpen(false)}
        aria-hidden="true"
      />
      <HistorySidebar
        conversations={conversations}
        activeId={activeId}
        open={historyOpen}
        collapsed={sidebarCollapsed}
        busy={sending || listening || transcribing || processingImages}
        onClose={() => setHistoryOpen(false)}
        onToggleCollapsed={() => {
          setSidebarCollapsed((current) => {
            const next = !current;
            localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
            return next;
          });
        }}
        onCreate={() => void createConversation()}
        onSelect={(conversationId) => void openConversation(conversationId)}
        onDelete={(conversationId) => void deleteConversation(conversationId)}
      />
      <ChatPanel
        title={active?.title ?? "新会话"}
        messages={messages}
        value={input}
        selectedImages={selectedImages}
        processingImages={processingImages}
        sending={sending}
        listening={listening}
        transcribing={transcribing}
        speechSupported={audioRecordingSupported()}
        error={error}
        onValueChange={(value) => {
          setInput(value);
          if (inputMode !== "voice" && !selectedImages.length) setInputMode("text");
        }}
        onSelectImages={selectImages}
        onRemoveImage={removeImage}
        onStartRecording={() => void startVoiceRecording()}
        onReleaseRecording={releaseVoiceRecording}
        onCancelRecording={cancelRecording}
        onRecordingUnavailable={() =>
          setError(
            window.isSecureContext
              ? "当前浏览器不支持录音，请更新浏览器或改用文字输入。"
              : "当前地址无法使用麦克风。电脑请打开 localhost，手机请通过 HTTPS 访问。",
          )
        }
        onCameraUnavailable={() =>
          setError(
            window.isSecureContext
              ? "当前浏览器不支持摄像头，请检查设备权限。"
              : "电脑拍照需要 HTTPS 或 localhost，请使用安全地址访问。",
          )
        }
        onSubmit={() => void submitMessage()}
        onStop={stopGeneration}
        onOpenHistory={() => setHistoryOpen(true)}
        onSuggestion={(value) => {
          setInput(value);
          setInputMode(selectedImages.length ? "multimodal" : "text");
        }}
      />
    </main>
  );
}
