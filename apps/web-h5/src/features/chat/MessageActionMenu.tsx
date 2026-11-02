import { Copy, Volume2 } from "lucide-react";
import { useEffect } from "react";
import { createPortal } from "react-dom";

async function copyText(value: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch { /* Older embedded WebViews may expose Clipboard API but reject it. */ }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.style.cssText = "position:fixed;opacity:0;pointer-events:none";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

export function MessageActionMenu({ content, open, anchor, onClose, onRead }: {
  content: string; open: boolean; onClose: () => void;
  anchor: { x: number; y: number };
  onRead: (content: string) => void;
}) {
  useEffect(() => {
    if (!open) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      onClose();
    };
    const dismiss = () => onClose();
    window.addEventListener("keydown", keydown);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
    };
  }, [onClose, open]);

  if (!open) return null;

  async function copy() {
    await copyText(content);
    onClose();
  }

  function read() {
    onRead(content);
    onClose();
  }

  const below = anchor.y < 104;
  const position = {
    left: `clamp(82px, ${anchor.x}px, calc(100vw - 82px))`,
    top: `${anchor.y + (below ? 12 : -12)}px`,
  };

  return createPortal(<div className="message-menu-backdrop" onPointerDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className={`message-menu${below ? " is-below" : ""}`} role="menu"
      aria-label="消息操作" style={position} onPointerDown={(event) => event.stopPropagation()}>
      <button type="button" role="menuitem" onClick={() => void copy()} aria-label="复制消息">
        <Copy size={17} />
        <span>复制</span>
      </button>
      <i aria-hidden="true" />
      <button type="button" role="menuitem" onClick={read} aria-label="朗读消息">
        <Volume2 size={17} />
        <span>朗读</span>
      </button>
    </section>
  </div>, document.body);
}
