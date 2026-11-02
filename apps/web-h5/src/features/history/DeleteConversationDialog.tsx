import { AlertTriangle, Trash2, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

interface DeleteConversationDialogProps {
  title: string;
  deleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function DeleteConversationDialog({
  title,
  deleting,
  onCancel,
  onConfirm,
}: DeleteConversationDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !deleting) onCancel();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [deleting, onCancel]);

  return createPortal(
    <div className="confirm-overlay" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !deleting) onCancel();
    }}>
      <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
        <button className="confirm-close" type="button" onClick={onCancel} disabled={deleting} aria-label="关闭">
          <X size={19} />
        </button>
        <div className="confirm-icon" aria-hidden="true"><AlertTriangle size={22} /></div>
        <div className="confirm-copy">
          <h2 id="delete-dialog-title">删除这条会话？</h2>
          <p>“{title}”中的消息和已上传图片会同时删除，且无法恢复。</p>
        </div>
        <div className="confirm-actions">
          <button ref={cancelRef} className="confirm-cancel" type="button" onClick={onCancel} disabled={deleting}>取消</button>
          <button className="confirm-delete" type="button" onClick={onConfirm} disabled={deleting}>
            <Trash2 size={16} />{deleting ? "正在删除" : "删除会话"}
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
