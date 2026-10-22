import { useEffect, useState } from "react";

export function VoiceActivity({ listening, ready, transcribing, cancelling, level }: {
  listening: boolean; ready: boolean; transcribing: boolean; cancelling: boolean; level: number;
}) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!listening || !ready) return;
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 250);
    return () => window.clearInterval(timer);
  }, [listening, ready]);
  const status = transcribing ? "正在识别语音" : !ready ? "正在开启麦克风" : cancelling ? "松开取消发送" : "正在聆听";
  return <div className={`voice-activity ${cancelling ? "is-cancelling" : ""} ${transcribing ? "is-transcribing" : ""}`}>
    <div className="voice-activity-title"><strong role="status">{status}</strong><span>{listening && ready ? `00:${String(seconds).padStart(2, "0")}` : ""}</span></div>
    <div className="voice-waveform" aria-hidden="true">{Array.from({ length: 23 }, (_, index) => <i key={index} style={{ height: `${5 + level * (12 + (index * 7 % 23))}px`, animationDelay: `${index * 45}ms` }} />)}</div>
    <p>{transcribing ? "识别完成后自动发送" : !ready ? "请允许浏览器使用麦克风" : cancelling ? "本次录音不会发送" : "松开发送 · 上滑取消"}</p>
  </div>;
}
