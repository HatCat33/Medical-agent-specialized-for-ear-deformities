import { Camera, RefreshCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface CameraCaptureProps {
  onCapture: (file: File) => void;
  onClose: () => void;
}

type FacingMode = "environment" | "user";

function readableCameraError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") return "未获得相机权限，请在浏览器设置中允许访问。";
    if (error.name === "NotFoundError") return "没有检测到可用的摄像头。";
    if (error.name === "NotReadableError") return "摄像头正被其他应用占用，请关闭后重试。";
  }
  return "相机暂时无法启动，请检查摄像头权限或改用上传图片。";
}

export function CameraCapture({ onCapture, onClose }: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [facingMode, setFacingMode] = useState<FacingMode>("environment");
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function startCamera() {
      setReady(false);
      setError(null);
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: facingMode } },
          audio: false,
        });
        if (!active) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
          if (active) setReady(true);
        }
      } catch (cameraError) {
        if (active) setError(readableCameraError(cameraError));
      }
    }

    void startCamera();
    return () => {
      active = false;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, [facingMode]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  function takePhoto() {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) return;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    if (!context) {
      setError("照片生成失败，请重试。");
      return;
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setError("照片生成失败，请重试。");
          return;
        }
        const now = Date.now();
        onCapture(
          new File([blob], `camera-${now}.jpg`, {
            type: "image/jpeg",
            lastModified: now,
          }),
        );
        onClose();
      },
      "image/jpeg",
      0.9,
    );
  }

  return (
    <div className="camera-overlay" role="presentation" onMouseDown={onClose}>
      <section
        className="camera-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="电脑拍照"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="camera-header">
          <strong>电脑拍照</strong>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭相机">
            <X size={20} />
          </button>
        </header>
        <div className="camera-viewport">
          <video ref={videoRef} autoPlay muted playsInline />
          {!ready && !error && <span className="camera-status">正在启动相机...</span>}
          {error && <p className="camera-error">{error}</p>}
        </div>
        <div className="camera-actions">
          <button
            type="button"
            className="camera-secondary-button"
            onClick={() => setFacingMode((current) => (current === "environment" ? "user" : "environment"))}
            disabled={Boolean(error)}
          >
            <RefreshCw size={18} />
            切换摄像头
          </button>
          <button
            type="button"
            className="camera-primary-button"
            onClick={takePhoto}
            disabled={!ready}
          >
            <Camera size={18} />
            拍摄
          </button>
        </div>
      </section>
    </div>
  );
}
