# 语音转写接口

## 转写录音

`POST /api/v1/speech/transcriptions`，请求类型为 `multipart/form-data`，字段名为 `audio`。

支持 WebM、Ogg、WAV、MP3 和 M4A/MP4 音频容器，服务端按文件签名校验，默认最大 10 MB。成功响应：

```json
{
  "text": "第一次就诊需要准备什么"
}
```

后端通过 `ASR_PROVIDER`、`ASR_BASE_URL`、`ASR_API_KEY`、`ASR_MODEL`、`ASR_LANGUAGE` 和 `ASR_TIMEOUT_SECONDS` 选择模型。当前适配 OpenAI-compatible `POST /audio/transcriptions`。

原始音频只在请求内存中存在，完成转写后释放，不创建媒体记录、不进入会话历史，也不写入日志。转写文字只有在随后发送消息时才进入历史。
