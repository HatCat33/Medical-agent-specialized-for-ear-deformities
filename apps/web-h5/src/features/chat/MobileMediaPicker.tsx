import { Camera, Images, X } from "lucide-react";
import { useEffect } from "react";
import { createPortal } from "react-dom";

interface MobileMediaPickerProps {
  open: boolean;
  remaining: number;
  onCamera: () => void;
  onGallery: () => void;
  onClose: () => void;
}

export function MobileMediaPicker({ open, remaining, onCamera, onGallery, onClose }: MobileMediaPickerProps) {
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose, open]);

  if (!open) return null;

  return createPortal(
    <div className="media-sheet-backdrop" role="presentation">
      <section className="media-sheet" role="dialog" aria-modal="true" aria-labelledby="media-sheet-title">
        <div className="media-sheet-handle" aria-hidden="true" />
        <header>
          <div><strong id="media-sheet-title">添加图片</strong><span>还可以添加 {remaining} 张</span></div>
          <button type="button" onClick={onClose} aria-label="关闭图片选择"><X size={20} /></button>
        </header>
        <div className="media-sheet-actions">
          <button type="button" onClick={onCamera}>
            <span className="media-sheet-icon"><Camera size={21} /></span>
            <span><strong>拍照</strong><small>使用手机相机拍摄</small></span>
          </button>
          <button type="button" onClick={onGallery}>
            <span className="media-sheet-icon"><Images size={21} /></span>
            <span><strong>从相册选择</strong><small>支持一次选择多张图片</small></span>
          </button>
        </div>
      </section>
    </div>,
    document.body,
  );
}
