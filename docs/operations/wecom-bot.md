# 企业微信智能机器人 API 模式

本项目接入的是企业微信智能机器人 **API 模式（WebSocket 长连接）**，使用 `Bot ID + Secret`，不是群机器人 Webhook，也不是 `Corp ID + Agent ID` 自建应用。官方协议说明见 [企业微信开发文档](https://developer.work.weixin.qq.com/document/path/101463)，实现遵循官方 [Node.js SDK](https://github.com/WecomTeam/aibot-node-sdk) 的帧格式。

## 能力范围

- 无需公网回调地址，后端主动连接 `wss://openws.work.weixin.qq.com`。
- 支持单聊文字、企业微信已完成转写的语音、单张图片和图文混排消息。
- 语音使用企业微信回调中的 `voice.content` 转写结果；API 模式不提供原始语音文件，因此不会调用网页端配置的 `ASR_*` 模型。转写为空时会提示用户重新录音或改用文字，不会静默丢弃。
- 图片按企业微信协议下载并使用消息携带的 AES Key 解密，再进入现有私有媒体与视觉模型链路。
- 兼容企业微信返回的无 Base64 填充 AES Key；带临时签名的媒体下载 URL 不写入应用日志。
- 回复使用企业微信 `stream` 消息累计刷新，沿用患者端相同的安全规则、RAG、提示词、多轮上下文、滚动摘要和问答日志。
- 企业微信 `msgid` 持久化去重，断线重投不会重复生成回答。
- 文件和视频问答暂不处理，会返回明确提示。
- 群聊默认关闭；医疗咨询建议保持单聊。

## 配置

不要把 Secret 写进 Git，也不要通过聊天或截图发送。在 `backend/.env` 填写：

```dotenv
WECOM_BOT_ENABLED=true
WECOM_BOT_ID=企业微信后台显示的BotID
WECOM_BOT_SECRET=企业微信后台显示的Secret
WECOM_BOT_WS_URL=wss://openws.work.weixin.qq.com
WECOM_BOT_ALLOWED_USER_IDS=
WECOM_BOT_ALLOW_GROUPS=false
WECOM_BOT_HEARTBEAT_SECONDS=30
WECOM_BOT_REPLY_INTERVAL_SECONDS=0.3
```

`WECOM_BOT_ALLOWED_USER_IDS` 留空表示允许所有能够联系机器人的单聊用户；试运行阶段建议填写逗号分隔的企业微信 User ID 白名单。私有化企业如管理后台提供了专用长连接地址，将其填入 `WECOM_BOT_WS_URL`，必须使用 `wss://`。

## 启动与检查

API 模式同一个机器人同一时间只应运行一个长连接实例，因此当前后端保持单进程、单 worker：

```powershell
cd F:\medical_agent\backend
uv run python scripts/upgrade_database.py
uv run uvicorn medical_agent.main:app --host 0.0.0.0 --port 8000
```

认证成功后日志出现 `wecom_bot_authenticated`。管理员可查询不含密钥的状态：

```powershell
$headers = @{ "X-Admin-Token" = "你的ADMIN_API_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/api/v1/admin/channels/wecom/status -Headers $headers
```

状态中的 `authenticated=true` 才表示 Bot ID、Secret、网络和长连接认证均正常。`last_error` 用于定位最近一次认证或断线原因，但不会返回 Secret。

## 数据与日志

- 企微用户 ID 只用于生成不可逆的内部会话归属值；应用日志仅记录其 SHA-256 短摘要。
- 对话正文是否写入问答追踪日志仍由 `INTERACTION_LOG_INCLUDE_CONTENT` 控制。
- 图片进入 `MEDIA_STORAGE_PATH` 私有目录并绑定到当前会话，不公开为静态资源。
- 一个企微单聊稳定对应一个内部会话，不与网页端或其他企微用户共享长期记忆。
