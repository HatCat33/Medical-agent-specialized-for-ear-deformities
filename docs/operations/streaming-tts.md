# 云端流式语音与文字展示

## 当前使用云端

`backend/.env` 使用 `TTS_PROTOCOL=openai-compatible`、`TTS_STREAMING_ENABLED=true`，沿用已配置的云端模型、地址及密钥。本地 CPU 模型仅保留为可选实验能力，不是当前默认播报方式。

管理入口统一为 **运行总览 → 语音合成 → 配置**。“模型连接”修改协议、地址、密钥、模型和流式开关；保存后进入“音色与播报”，配置预设或自定义音色、默认出声、语速、音量，并试听。顶部不再单列“声音设置”。音色留空使用环境 / 模型配置中的默认值，非空则覆盖默认音色；更换模型后请检查音色是否匹配。

## 低延迟云端推荐

中文患者场景优先使用 Azure Speech。管理端选择 `Azure Speech` 后会自动开启流式 PCM；填写与 Speech 资源一致的区域端点和 Key，保存后在“音色与播报”选择 `zh-CN-XiaoxiaoNeural` 等中文音色。示例：

```env
TTS_PROTOCOL=azure-speech
TTS_BASE_URL=https://eastasia.tts.speech.microsoft.com
TTS_API_KEY=替换为 Azure Speech Key
TTS_MODEL=Azure Neural TTS
TTS_VOICE=zh-CN-XiaoxiaoNeural
TTS_STREAMING_ENABLED=true
TTS_STREAM_RESPONSE_FORMAT=pcm
TTS_STREAM_SAMPLE_RATE=24000
TTS_STREAM_CHANNELS=1
```

区域端点必须与 Azure 控制台中 Speech 资源的区域一致，不能直接照抄 `eastasia`。备选方案是在管理端选择 `ElevenLabs`，系统会预填 `https://api.elevenlabs.io`、`eleven_flash_v2_5` 和流式开关；还需填写 ElevenLabs API Key，并在“音色与播报”填写 Voice ID。

国内部署优先可选择 `火山引擎豆包语音`。项目使用 V3 HTTP SSE 单向流式接口，将服务端返回的 Base64 PCM 实时解码并转发给患者端。新版控制台只需 API Key，不需要 App ID；模型字段填写资源 ID，音色字段填写已开通的 Speaker ID：

```env
TTS_PROTOCOL=volcengine
TTS_BASE_URL=https://openspeech.bytedance.com
TTS_API_KEY=替换为新版控制台API Key
TTS_MODEL=seed-tts-2.0
TTS_VOICE=替换为已授权Speaker ID
TTS_STREAMING_ENABLED=true
TTS_STREAM_SAMPLE_RATE=24000
TTS_STREAM_CHANNELS=1
```

## 可选本地调试方案

`backend/.env` 可选择 `TTS_PROTOCOL=local-sherpa`，由后端 CPU 运行中文 Huayan ONNX 模型，浏览器和手机仍通过统一 PCM 流接口播放。无需为患者设备安装语音包，也无需新的云端密钥。现有云端参数可以保留，切换协议即可继续使用。

首次安装（在 backend 目录）：

```powershell
uv sync --extra dev --extra local-tts
uv run --extra local-tts python scripts/setup_local_tts.py
```

本地配置：

```dotenv
TTS_PROTOCOL=local-sherpa
TTS_STREAMING_ENABLED=true
TTS_STREAM_SAMPLE_RATE=24000
TTS_STREAM_CHANNELS=1
TTS_LOCAL_MODEL_DIR=./data/tts/huayan
TTS_LOCAL_THREADS=2
TTS_LOCAL_SPEAKER_ID=0
```

启动时保留 local-tts extra，模型在应用启动阶段加载并预热：

```powershell
uv run --extra local-tts uvicorn medical_agent.main:app --host 0.0.0.0 --port 8000
```

模型文件位于 `backend/data/tts/huayan`，已被 Git 忽略。下载脚本固定到每次查询得到的仓库 revision，并校验文件长度和 LFS SHA-256。迁移服务器时需要重新安装模型及 local-tts extra；不要仅上传代码后期待模型存在。Linux/Windows 可使用相同 Python 实现，但性能需要在目标 CPU 上复测。

声音设置仍选择服务端播报（内部兼容旧的 `provider=openai-compatible` 值）；实际接口协议取决于 `TTS_PROTOCOL`。本地模型使用固定中文音色，忽略旧云端音色 ID，支持语速/音量，不支持音色克隆。管理端已保存的模型配置优先于 `.env`，可以在运行总览中选择 `local-sherpa` 或恢复环境默认值。

Huayan 模型卡标记训练数据许可证为 **Unknown**，这条配置用于本地内部效果验证，不能据此认定已获医院正式部署授权。正式部署应选择已获授权的音色模型或商业 TTS。语音测试不能替代对“耳廓”等医学词、数字及否定句的人工试听。

## 文字与音频如何输出

- 首句使用短语义片段尽早合成；之后合并为较完整的句子，避免反复请求 5～10 字碎片。
- 最多提前准备三段音频；已排程超过约 12 秒时暂停继续发起请求，限制积压。
- 浏览器用同一 AudioContext 时间线连接 PCM：起播先积累约 1 秒音频，再保留约 240ms 排程余量，吸收网络波动。缓冲耗尽时先积累约 800ms 再恢复，避免每收到一点就短暂播放；不足阈值的末尾音频在流结束时照常播放。这是音频时长，不是固定额外等待时间，云端生成较快时可以很快攒足。
- 浏览器控制台的 `answer_speech_buffer_underrun` 仅记录断供时长和恢复缓冲时长，不记录问答内容。持续慢于播放速度的云端生成仍可能停顿，增加缓冲不能保证消除任意网络延迟。
- 文字独立按每 55ms 一个 Unicode 字符展示，首段等待约 180ms，不受语音进度、静音或声音加载失败影响。上游分片再大也不批量追赶。
- 语音从原始模型增量提前合成，文字可以先出现；不要求逐字同步，也不为了等声音停住文字。
- 播放中断会保留已排程但尚未听到的句子用于降级，可能从当前句重新播，不会静默漏掉后续内容。

## 云端流式修复

SiliconFlow 的 `/audio/speech` 需要明确传 `stream=true` 和 `sample_rate`。`response_format=pcm` 本身不能证明服务在逐步生成音频。后端现在为 SiliconFlow 加入这两个参数；其他协议不盲目添加不支持的字段。

日志 `speech_stream_first_audio.first_audio_ms` 表示首包等待；`speech_stream_completed.upstream_ms` 表示整个请求耗时。后者不等于开始播报的等待。返回 HTTP 200、收到多个 TCP 分片，也都不能单独证明低延迟。

## 复测

本地模型合成测试：

```powershell
uv run --extra local-tts python scripts/probe_local_tts.py
```

加 `--check-asr` 会把固定测试文字生成的音频发往已配置 ASR，产生一次识别请求，打印识别文本以辅助检查读音；不发送患者资料。

真实浏览器测试使用独立的仅监听回环地址的语音测试进程，不连接企微机器人、不创建患者会话。先运行：

```powershell
uv run --extra local-tts python scripts/serve_tts_probe.py
```

另一个终端在项目根目录运行（要求本地 H5 已启动）：

```powershell
node tests/e2e/real-tts-stream-smoke.mjs
```

测试使用真实合成音频，记录首音时已显示的字数，检查声音源进入播放并结束、句间排程间隔及是否发生降级。文字不等待语音，云端延迟较高时可以先完成展示。此测试不代表所有手机的扬声器和系统权限已经验收。
