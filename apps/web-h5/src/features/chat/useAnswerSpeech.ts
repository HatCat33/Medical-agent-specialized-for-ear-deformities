import { useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import { AnswerPlayback, type SpeechSettings } from "../../lib/playback";

// Versioned once so the new administrator default is not masked by stale pre-v3 mute state.
const PREFERENCE_KEY = "medical-agent-auto-speech-v2";

export function useAnswerSpeech() {
  const [enabled, setEnabled] = useState(() => localStorage.getItem(PREFERENCE_KEY) !== "false");
  const [notice, setNotice] = useState("");
  const [cloud, setCloud] = useState(false);
  const [playback] = useState(() => {
    const instance = new AnswerPlayback((usingDeviceFallback, reason) => {
      console.warn("answer_speech_playback_failed", reason);
      setNotice(
        usingDeviceFallback && "speechSynthesis" in window
          ? "云端声音暂时不可用，正在尝试设备语音。"
          : "声音暂时未能播放，请检查音频权限或稍后重试。",
      );
    }, api.synthesizeSpeech, () => setNotice(""), api.streamSpeech);
    instance.enabled = localStorage.getItem(PREFERENCE_KEY) !== "false";
    return instance;
  });
  const preferenceChanged = useRef(false);
  const supported = cloud || "speechSynthesis" in window;

  useEffect(() => {
    let active = true;
    void api.speechSettings<SpeechSettings>().then(settings => {
      if (!active) return;
      playback.configure(settings);
      setCloud(settings.provider === "openai-compatible");
      if (!preferenceChanged.current && localStorage.getItem(PREFERENCE_KEY) === null) {
        playback.setEnabled(settings.auto_play); setEnabled(settings.auto_play);
      }
    }).catch(() => { /* Device defaults remain available when settings cannot be loaded. */ });
    return () => { active = false; playback.cancel(); };
  }, [playback]);

  useEffect(() => {
    const unlock = () => playback.unlock();
    document.addEventListener("pointerdown", unlock, { passive: true });
    document.addEventListener("touchend", unlock, { passive: true });
    document.addEventListener("keydown", unlock);
    document.addEventListener("WeixinJSBridgeReady", unlock);
    return () => {
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("touchend", unlock);
      document.removeEventListener("keydown", unlock);
      document.removeEventListener("WeixinJSBridgeReady", unlock);
    };
  }, [playback]);

  function toggle() {
    preferenceChanged.current = true;
    const next = !enabled;
    playback.setEnabled(next);
    if (next) playback.unlock();
    localStorage.setItem(PREFERENCE_KEY, String(next)); setEnabled(next); setNotice("");
  }

  return { enabled, supported, notice, playback, toggle };
}
