import { useCallback, useEffect, useRef, useState } from "react";

import { ChatPanel } from "../features/chat/ChatPanel";
import { useAnswerSpeech } from "../features/chat/useAnswerSpeech";
import { HistorySidebar } from "../features/history/HistorySidebar";
import { DeleteConversationDialog } from "../features/history/DeleteConversationDialog";
import { api } from "../lib/api";
import { prepareImageForUpload } from "../lib/image";
import { ProgressiveTextStream } from "../lib/progressiveStream";
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

interface ActiveTurn {
  id: string;
  conversationId: string | null;
  controller: AbortController;
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
  const speech = useAnswerSpeech();
  const playback = speech.playback;
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [inputMode, setInputMode] = useState<InputMode>("text");
  const [selectedImages, setSelectedImages] = useState<SelectedImage[]>([]);
  const [processingImages, setProcessingImages] = useState(false);
  const [sending, setSending] = useState(false);
  const [syncingConversationId, setSyncingConversationId] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [recordingReady, setRecordingReady] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingConversation, setDeletingConversation] = useState(false);
  const historyOpenRef = useRef(false);
  const appShellRef = useRef<HTMLElement>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true",
  );
  const streamController = useRef<AbortController | null>(null);
  const recordingController = useRef<AudioRecordingController | null>(null);
  const recordingAttempt = useRef(0);
  const recordingReleasePending = useRef(false);
  const recordingTimer = useRef<number | null>(null);
  const previewUrls = useRef(new Set<string>());
  const conversationCreation = useRef<Promise<Conversation> | null>(null);
  const conversationOpenAttempt = useRef(0);
  const syncingConversationRef = useRef<string | null>(null);
  const activeTurnRef = useRef<ActiveTurn | null>(null);

  const updateSyncingConversation = useCallback((conversationId: string | null) => {
    syncingConversationRef.current = conversationId;
    setSyncingConversationId(conversationId);
  }, []);

  const invalidateActiveTurn = useCallback(() => {
    const turn = activeTurnRef.current;
    activeTurnRef.current = null;
    if (turn) {
      turn.controller.abort();
      playback.cancel(turn.id);
      if (streamController.current === turn.controller) streamController.current = null;
    } else {
      playback.cancel();
    }
    setSending(false);
  }, [playback]);

  useEffect(() => {
    historyOpenRef.current = historyOpen;
  }, [historyOpen]);

  useEffect(() => {
    let startX = 0;
    let startY = 0;
    let lastX = 0;
    let lastTime = 0;
    let velocityX = 0;
    let dragOffset = 0;
    let gesture: "pending" | "horizontal" | "vertical" = "pending";
    let eligible = false;

    const sidebarWidth = () => Math.min(window.innerWidth * 0.86, 320);
    const updateDrag = (clientX: number) => {
      const shell = appShellRef.current;
      if (!shell) return;
      const width = sidebarWidth();
      const baseOffset = historyOpenRef.current ? width : 0;
      dragOffset = Math.min(width, Math.max(0, baseOffset + clientX - startX));
      shell.style.setProperty("--mobile-history-offset", `${dragOffset}px`);
      shell.style.setProperty("--mobile-history-progress", String(dragOffset / width));
    };
    const finishDrag = () => {
      if (gesture !== "horizontal") return;
      const shell = appShellRef.current;
      const width = sidebarWidth();
      const projectedOffset = dragOffset + velocityX * 140;
      const shouldOpen = projectedOffset >= width * 0.5;
      historyOpenRef.current = shouldOpen;
      shell?.classList.toggle("is-history-open", shouldOpen);
      setHistoryOpen(shouldOpen);
      window.requestAnimationFrame(() => {
        shell?.classList.remove("is-history-dragging");
        shell?.style.removeProperty("--mobile-history-offset");
        shell?.style.removeProperty("--mobile-history-progress");
      });
    };
    const onTouchStart = (event: TouchEvent) => {
      if (!window.matchMedia("(max-width: 760px)").matches || event.touches.length !== 1) return;
      const touch = event.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      lastX = touch.clientX;
      lastTime = performance.now();
      velocityX = 0;
      dragOffset = historyOpenRef.current ? sidebarWidth() : 0;
      gesture = "pending";
      eligible = historyOpenRef.current || startX <= Math.min(window.innerWidth * 0.68, 280);
    };
    const onTouchMove = (event: TouchEvent) => {
      if (!eligible || !window.matchMedia("(max-width: 760px)").matches || event.touches.length !== 1) return;
      const touch = event.touches[0];
      const deltaX = touch.clientX - startX;
      const deltaY = touch.clientY - startY;
      if (gesture === "pending" && Math.max(Math.abs(deltaX), Math.abs(deltaY)) >= 8) {
        gesture = Math.abs(deltaX) > Math.abs(deltaY) * 1.15 ? "horizontal" : "vertical";
        if (gesture === "horizontal") appShellRef.current?.classList.add("is-history-dragging");
      }
      if (gesture !== "horizontal") return;
      if (event.cancelable) event.preventDefault();
      const now = performance.now();
      const elapsed = now - lastTime;
      if (elapsed > 0) {
        const instantVelocity = (touch.clientX - lastX) / elapsed;
        velocityX = velocityX * 0.65 + instantVelocity * 0.35;
      }
      lastX = touch.clientX;
      lastTime = now;
      updateDrag(touch.clientX);
    };
    const onTouchEnd = (event: TouchEvent) => {
      if (!event.changedTouches.length) return;
      if (gesture === "horizontal") updateDrag(event.changedTouches[0].clientX);
      finishDrag();
      gesture = "pending";
      eligible = false;
    };
    const onTouchCancel = () => {
      velocityX = 0;
      finishDrag();
      gesture = "pending";
      eligible = false;
    };
    document.addEventListener("touchstart", onTouchStart, { passive: true });
    document.addEventListener("touchmove", onTouchMove, { passive: false });
    document.addEventListener("touchend", onTouchEnd, { passive: true });
    document.addEventListener("touchcancel", onTouchCancel, { passive: true });
    return () => {
      document.removeEventListener("touchstart", onTouchStart);
      document.removeEventListener("touchmove", onTouchMove);
      document.removeEventListener("touchend", onTouchEnd);
      document.removeEventListener("touchcancel", onTouchCancel);
    };
  }, []);

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
    setRecordingReady(false); setAudioLevel(0);
  }, []);

  const readMessage = useCallback((content: string) => playback.read(content), [playback]);

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
      const attempt = ++conversationOpenAttempt.current;
      invalidateActiveTurn();
      updateSyncingConversation(null);
      setError(null);
      setInput("");
      setInputMode("text");
      cancelRecording();
      clearSelectedImages();
      setActiveId(conversationId);
      setHistoryOpen(false);
      const loadDetail = async () => {
        try {
          return await api.getConversation(conversationId);
        } catch (reason) {
          if (conversationOpenAttempt.current === attempt) updateSyncingConversation(null);
          throw reason;
        }
      };
      let detail = await loadDetail();
      if (conversationOpenAttempt.current !== attempt) return;
      setMessages(detail.messages);
      if (detail.messages.at(-1)?.role !== "user" || detail.generation_pending !== true) {
        updateSyncingConversation(null);
        if (detail.messages.at(-1)?.role === "user") {
          setError("上一条回答未完成，你可以重新发送问题。");
        }
        return;
      }
      updateSyncingConversation(conversationId);
      // A disconnected SSE request may still be finishing on the server. Refresh the
      // open conversation until the recovered answer appears without a manual reload.
      for (
        let retry = 0;
        retry < 120
        && detail.messages.at(-1)?.role === "user"
        && detail.generation_pending === true;
        retry += 1
      ) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        if (conversationOpenAttempt.current !== attempt) return;
        detail = await loadDetail();
        if (conversationOpenAttempt.current !== attempt) return;
        setMessages(detail.messages);
      }
      if (conversationOpenAttempt.current !== attempt) return;
      if (detail.messages.at(-1)?.role === "user") {
        setError(detail.generation_pending
          ? "上一条回答同步超时，请稍后重新进入此会话查看。"
          : "上一条回答未完成，你可以重新发送问题。",
        );
      }
      updateSyncingConversation(null);
    },
    [cancelRecording, clearSelectedImages, invalidateActiveTurn, updateSyncingConversation],
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
    if (conversationCreation.current) return conversationCreation.current;
    if (!preserveComposer) {
      const existingEmpty = conversations.find((item) => item.last_message_at === null);
      if (existingEmpty) {
        if (existingEmpty.id !== activeId) await openConversation(existingEmpty.id);
        return existingEmpty;
      }
    }
    // Invalidate any history-sync polling from the conversation being left.
    conversationOpenAttempt.current += 1;
    if (!preserveComposer) {
      invalidateActiveTurn();
      setInput("");
      setInputMode("text");
      cancelRecording();
      clearSelectedImages();
    }
    const request = api.createConversation();
    conversationCreation.current = request;
    try {
      const conversation = await request;
      // The API reuses one empty conversation and removes older empty duplicates.
      setConversations((current) => [
        conversation,
        ...current.filter((item) => item.id !== conversation.id && item.last_message_at !== null),
      ]);
      setActiveId(conversation.id);
      setMessages([]);
      updateSyncingConversation(null);
      setHistoryOpen(false);
      return conversation;
    } finally {
      conversationCreation.current = null;
    }
  }

  async function deleteConversation() {
    const conversationId = pendingDeleteId;
    if (!conversationId || deletingConversation) return;
    setDeletingConversation(true);
    try {
      await api.deleteConversation(conversationId);
      const remaining = conversations.filter((item) => item.id !== conversationId);
      setConversations(remaining);
      if (activeId === conversationId) {
        invalidateActiveTurn();
        if (remaining[0]) await openConversation(remaining[0].id);
        else {
          conversationOpenAttempt.current += 1;
          setActiveId(null);
          setMessages([]);
          updateSyncingConversation(null);
          clearSelectedImages();
        }
      }
      setPendingDeleteId(null);
    } catch {
      setError("会话删除失败，请稍后重试。");
    } finally {
      setDeletingConversation(false);
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
    playback.cancel();
    playback.unlock();
    const attempt = recordingAttempt.current + 1;
    recordingAttempt.current = attempt;
    recordingReleasePending.current = false;
    setError(null);
    setListening(true);
    try {
      const controller = await startAudioRecording(level => {
        if (recordingAttempt.current === attempt) setAudioLevel(level);
      });
      if (recordingAttempt.current !== attempt) {
        controller.cancel();
        return;
      }
      recordingController.current = controller;
      setRecordingReady(true);
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
    setRecordingReady(false); setAudioLevel(0);
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
      setTranscribing(false);
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
    if (
      (!content && !files.length)
      || sending
      || processingImages
      || (
        syncingConversationRef.current !== null
        && syncingConversationRef.current === activeId
      )
    ) return;
    cancelRecording();
    setSending(true);
    setError(null);
    const controller = new AbortController();
    const turn: ActiveTurn = {
      id: crypto.randomUUID(),
      conversationId: activeId,
      controller,
    };
    activeTurnRef.current = turn;
    streamController.current = controller;
    playback.begin(false, turn.id);

    let uploaded: MediaAttachment[] = [];
    let userPersisted = false;
    let userTemporaryId = "";
    let assistantTemporaryId = "";
    let presenter: ProgressiveTextStream | null = null;
    const completedMessage: { current: Message | null } = { current: null };
    try {
      if (files.length) uploaded = await api.uploadImages(files);
      const conversation = activeId
        ? conversations.find((item) => item.id === activeId) ?? (await createConversation(true))
        : await createConversation(true);
      turn.conversationId = conversation.id;
      if (activeTurnRef.current !== turn || controller.signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
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
      presenter = new ProgressiveTextStream((chunk) => {
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantTemporaryId
              ? { ...item, content: item.content + chunk }
              : item,
          ),
        );
      }, controller.signal);

      await api.streamMessage(
        conversation.id,
        content,
        uploaded.map((item) => item.id),
        uploaded.length ? "multimodal" : (options?.inputMode ?? inputMode),
        {
          onUserMessage: (message) => {
            userPersisted = true;
            if (activeTurnRef.current !== turn) return;
            clearSelectedImages();
            setInputMode("text");
            setMessages((current) =>
              current.map((item) => (item.id === userTemporaryId ? message : item)),
            );
          },
          onDelta: (chunk) => {
            if (activeTurnRef.current !== turn) return;
            playback.append(chunk, turn.id);
            presenter?.push(chunk);
          },
          onDone: (message) => {
            if (activeTurnRef.current !== turn) return;
            completedMessage.current = message;
          },
        },
        controller.signal,
      );
      if (activeTurnRef.current !== turn) {
        throw new DOMException("Aborted", "AbortError");
      }
      playback.finish(turn.id);
      await presenter.finish();
      if (activeTurnRef.current !== turn || controller.signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      if (!completedMessage.current) throw new Error("stream_ended_without_message");
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantTemporaryId ? completedMessage.current! : item,
        ),
      );
    } catch (caught) {
      presenter?.cancel();
      playback.cancel(turn.id);
      const stopped = caught instanceof DOMException && caught.name === "AbortError";
      const ownsTurn = activeTurnRef.current === turn;
      if (!userPersisted) {
        if (ownsTurn) {
          setMessages((current) =>
            current.filter((item) => item.id !== userTemporaryId && item.id !== assistantTemporaryId),
          );
          setInput(content);
        }
        await Promise.allSettled(uploaded.map((item) => api.deleteMedia(item.id)));
      } else if (ownsTurn) {
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
      if (ownsTurn && !stopped) setError(userPersisted
        ? "回答连接中断，已保留发送成功的内容，请稍后重试。"
        : "暂时无法发送，输入内容已保留，请稍后重试。");
    } finally {
      if (activeTurnRef.current === turn) {
        activeTurnRef.current = null;
        if (streamController.current === controller) streamController.current = null;
        setSending(false);
        try {
          await refreshConversations();
        } catch {
          // The current message remains usable even if refreshing the sidebar fails.
        }
      }
    }
  }

  function stopGeneration() {
    const turn = activeTurnRef.current;
    if (!turn) return;
    playback.cancel(turn.id);
    turn.controller.abort();
  }

  const active = conversations.find((item) => item.id === activeId);
  const syncing = syncingConversationId !== null && syncingConversationId === activeId;

  return (
    <main
      ref={appShellRef}
      className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""} ${
        historyOpen ? "is-history-open" : ""
      }`}
    >
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
        onDelete={setPendingDeleteId}
      />
      <button
        className="mobile-history-dismiss"
        type="button"
        aria-label="返回对话"
        onClick={() => {
          historyOpenRef.current = false;
          setHistoryOpen(false);
        }}
      />
      <ChatPanel
        onCreateConversation={() => void createConversation()}
        canCreateConversation={!sending && !listening && !transcribing && !processingImages}
        autoSpeech={speech.enabled}
        playbackSupported={speech.supported}
        playbackNotice={speech.notice}
        onToggleSpeech={speech.toggle}
        onReadMessage={readMessage}
        title={active?.title ?? "新会话"}
        messages={messages}
        value={input}
        selectedImages={selectedImages}
        processingImages={processingImages}
        sending={sending}
        syncing={syncing}
        listening={listening}
        recordingReady={recordingReady}
        audioLevel={audioLevel}
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
        onImagePickerUnavailable={(reason) => {
          const message = reason?.includes("config")
            ? "企业微信 JS-SDK 鉴权失败，请检查当前患者端域名是否已加入该自建应用的 JS-SDK 可信域名。"
            : reason?.includes("api_unavailable")
              ? "当前企业微信版本未开放图片接口，请升级企业微信后重试。"
              : "企业微信图片能力暂时不可用，本次已回退设备原生方式。";
          setError(message);
        }}
        onSubmit={() => void submitMessage()}
        onStop={stopGeneration}
        onOpenHistory={() => setHistoryOpen(true)}
        onSuggestion={(value) => {
          void submitMessage({
            content: value,
            inputMode: selectedImages.length ? "multimodal" : "text",
          });
        }}
      />
      {pendingDeleteId && (
        <DeleteConversationDialog
          title={conversations.find(item => item.id === pendingDeleteId)?.title ?? "当前会话"}
          deleting={deletingConversation}
          onCancel={() => {
            if (!deletingConversation) setPendingDeleteId(null);
          }}
          onConfirm={() => void deleteConversation()}
        />
      )}
    </main>
  );
}
