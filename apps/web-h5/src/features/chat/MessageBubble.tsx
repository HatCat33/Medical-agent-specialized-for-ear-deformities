import { ImageOff } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "../../lib/api";
import { patientVisibleMarkdown } from "../../lib/markdown";
import type { MediaAttachment, Message } from "../../lib/types";
import { MessageActionMenu } from "./MessageActionMenu";

const markdownPlugins = [remarkGfm];

function SecureMessageImage({ attachment, index }: { attachment: MediaAttachment; index: number }) {
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    void api.mediaBlob(attachment.id).then(blob => {
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setSource(objectUrl);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [attachment.id]);
  if (failed) return <div className="message-image-failed" title="图片加载失败"><ImageOff size={19} /></div>;
  if (!source) return <div className="message-image-loading" aria-label="图片加载中" />;
  return <a href={source} target="_blank" rel="noreferrer" title="查看原图"><img src={source} alt={`用户上传的第 ${index + 1} 张图片`} /></a>;
}

const MessageAttachments = memo(function MessageAttachments({ attachments }: { attachments: MediaAttachment[] }) {
  const images = attachments.filter(item => item.kind === "image");
  if (!images.length) return null;
  return <div className={`message-images count-${Math.min(images.length, 4)}`}>
    {images.map((attachment, index) => <SecureMessageImage key={attachment.id} attachment={attachment} index={index} />)}
  </div>;
});

// Completed history must not parse Markdown again for every new character or audio-level update.
const MessageMarkdown = memo(function MessageMarkdown({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={markdownPlugins}>{content}</ReactMarkdown>;
});

export const MessageBubble = memo(function MessageBubble({ message, animate, onRead }: {
  message: Message; animate: boolean; onRead: (content: string) => void;
}) {
  const [animateOnMount] = useState(animate);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuAnchor, setMenuAnchor] = useState({ x: 0, y: 0 });
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const holdOrigin = useRef<{ x: number; y: number } | null>(null);
  const assistant = message.role === "assistant";
  const streaming = message.status === "streaming";
  const content = assistant ? patientVisibleMarkdown(message.content) : message.content;
  const images = message.attachments ?? [];

  function clearHold() {
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = null;
    holdOrigin.current = null;
  }

  function openMenu(anchor = holdOrigin.current) {
    if (!anchor) return;
    setMenuAnchor(anchor);
    clearHold();
    setMenuOpen(true);
    navigator.vibrate?.(12);
  }

  useEffect(() => () => {
    if (holdTimer.current) clearTimeout(holdTimer.current);
  }, []);

  return <article className={`message-row ${message.role}${animateOnMount ? " is-new" : ""}`}>
    <div className={`message-content${images.length ? " has-attachments" : ""}`}>
      {assistant ? <div className={`message-body${streaming ? " is-streaming" : ""}${!content && streaming ? " is-waiting" : ""}`}
        onPointerDown={(event) => {
          if (event.pointerType === "mouse" && event.button !== 0) return;
          clearHold(); holdOrigin.current = { x: event.clientX, y: event.clientY };
          holdTimer.current = setTimeout(openMenu, 480);
        }}
        onPointerMove={(event) => {
          const origin = holdOrigin.current;
          if (origin && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > 9) clearHold();
        }}
        onPointerUp={clearHold} onPointerCancel={clearHold} onPointerLeave={clearHold}
        onContextMenu={(event) => {
          event.preventDefault();
          if (content) openMenu({ x: event.clientX, y: event.clientY });
        }}>
        {!content && streaming ? <div className="typing" aria-label="正在生成回答"><i /><i /><i /></div>
          : <MessageMarkdown content={content} />}
      </div> : <div className="user-message-bubble"
        onPointerDown={(event) => {
          if (event.pointerType === "mouse" && event.button !== 0) return;
          clearHold(); holdOrigin.current = { x: event.clientX, y: event.clientY };
          holdTimer.current = setTimeout(openMenu, 480);
        }}
        onPointerMove={(event) => {
          const origin = holdOrigin.current;
          if (origin && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > 9) clearHold();
        }}
        onPointerUp={clearHold} onPointerCancel={clearHold} onPointerLeave={clearHold}
        onContextMenu={(event) => {
          event.preventDefault();
          if (content) openMenu({ x: event.clientX, y: event.clientY });
        }}>
        <MessageAttachments attachments={images} />
        <p>{content}</p>
      </div>}
      {message.status === "interrupted" && <small className="message-status">回答已中断</small>}
    </div>
    <MessageActionMenu content={content} open={menuOpen && Boolean(content)} anchor={menuAnchor}
      onClose={() => setMenuOpen(false)} onRead={onRead} />
  </article>;
});
