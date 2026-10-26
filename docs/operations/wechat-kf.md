# 微信客服接入

本模块接入企业微信管理后台中的“微信客服”，不是企业微信智能机器人 API 模式，也不是群机器人 Webhook。微信客服使用 `Corp ID + 自建应用 Secret + OpenKfId`，通过公网 HTTPS 回调通知服务端，再由服务端调用 `sync_msg` 拉取消息并调用 `send_msg` 回复。

官方协议参考：

- [接收消息和事件、读取消息](https://developer.work.weixin.qq.com/document/path/94670)
- [发送消息](https://developer.work.weixin.qq.com/document/path/94677)

## 已支持能力

- 微信客户的文字、图片、语音消息。
- 图片下载后进入现有私有媒体、视觉模型、当前会话图片上下文链路。
- 语音以 AMR 格式下载，通过 `backend/.env` 中的 `ASR_*` 兼容模型转写；原始语音不落盘。
- 同一 `OpenKfId + external_userid` 稳定映射到同一内部会话，复用提示词、RAG、安全规则、多轮上下文、滚动摘要和问答日志。
- 微信客服 `msgid` 持久化去重，`next_cursor` 按客服账号持久化，服务重启后继续增量拉取。
- 回复超过微信客服单条 2048 字节限制时按 UTF-8 安全分段。
- 管理状态只显示启用、配置、最近回调/同步/消息/发送时间和脱敏错误，不返回 Secret、Access Token、回调 Token 或媒体地址。

视频和文件首版会返回明确提示，不进入模型。微信客户端不允许机器人强制自动播放语音，也不提供边生成边播放的音频流；当前回复为文本。若以后发送 TTS 语音气泡，用户仍需在微信中点击播放。

## 企业微信后台准备

1. 在企业微信管理后台创建或选择一个自建应用，并把它配置为“微信客服-可调用接口的应用”。从 2023-12-01 起，不应使用旧的系统应用 Secret。
2. 将需要由 API 管理的微信客服账号设置为“通过 API 管理”，记录回调或官方账号列表接口返回的 `OpenKfId`。
3. 确保客服账号对应的接待人员在自建应用可见范围内。
4. 准备一个公网可访问的 HTTPS 地址。微信服务器不能访问 `localhost`、局域网 IP 或自签名 HTTPS。
5. 在微信客服回调配置中填写本系统回调 URL、Token 和 EncodingAESKey。

回调 URL 固定为：

```text
https://你的公网域名/api/v1/channels/wechat-kf/callback
```

本地联调可将后端 `8000` 端口通过 ngrok 暴露到公网，然后把 ngrok 提供的 HTTPS 域名填入上述 URL。ngrok 地址变化后，企业微信后台的回调 URL 也必须同步更新。

## 后端配置

只在被 Git 忽略的 `backend/.env` 填写真实值：

```dotenv
WECHAT_KF_ENABLED=true
WECHAT_KF_CORP_ID=企业ID
WECHAT_KF_SECRET=被授权自建应用的Secret
WECHAT_KF_OPEN_KFID=客服账号OpenKfId
WECHAT_KF_OPEN_KF_IDS=
WECHAT_KF_CALLBACK_TOKEN=回调配置中的Token
WECHAT_KF_CALLBACK_AES_KEY=回调配置中的EncodingAESKey
WECHAT_KF_API_BASE_URL=https://qyapi.weixin.qq.com
WECHAT_KF_ALLOWED_EXTERNAL_USER_IDS=
WECHAT_KF_REQUEST_TIMEOUT_SECONDS=20
```

一个客服账号填 `WECHAT_KF_OPEN_KFID`；多个账号可在 `WECHAT_KF_OPEN_KF_IDS` 中用英文逗号分隔，两项会合并。试运行可在 `WECHAT_KF_ALLOWED_EXTERNAL_USER_IDS` 填写允许测试的 `external_userid` 白名单，留空表示允许所有客户。

客服接入链接形如 `https://work.weixin.qq.com/kfid/kf...`。链接路径中的 `kf...` 是接入链接标识，**不一定是 API 回调中的 `OpenKfId`**。API 使用的值通常形如 `wk...`，应以解密后的回调 `OpenKfId` 或官方账号列表接口结果为准，不能直接截取接入链接。

首次配置时如果尚未取得 `OpenKfId`，可以暂时保持 `WECHAT_KF_OPEN_KFID` 和 `WECHAT_KF_OPEN_KF_IDS` 为空并启动后端。此时状态为 `waiting_for_open_kfid`，只开放企业微信所需的回调 URL 验证，不同步正式消息。完成回调 URL、企业可信 IP 和微信客服 API 应用授权后，通过账号列表接口取得 `OpenKfId`，写入 `.env` 并重启后端。

语音输入还需要配置：

```dotenv
ASR_PROVIDER=openai-compatible
ASR_BASE_URL=https://供应商兼容接口地址/v1
ASR_API_KEY=密钥
ASR_MODEL=模型名
ASR_LANGUAGE=zh
```

供应商必须接受 AMR 文件。若供应商不支持 AMR，语音消息会收到可见的失败提示，不会静默丢失。

## 数据库与启动

首次升级先执行迁移，再由用户自行启动后端：

```powershell
cd F:\medical_agent\backend
uv run python scripts/upgrade_database.py
uv run uvicorn medical_agent.main:app --host 0.0.0.0 --port 8000
```

回调校验成功后，发送一条微信客服消息。查看不含密钥的渠道状态：

```powershell
$headers = @{ "X-Admin-Token" = "你的ADMIN_API_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/api/v1/admin/channels/wechat-kf/status -Headers $headers
```

正常状态应为 `enabled=true`、`configured=true`、`state=ready`，收到消息后 `last_callback_at`、`last_sync_at`、`last_message_at` 和 `last_send_at` 会依次更新。日志事件前缀为 `wechat_kf_`；客户标识只记录哈希引用。

## 常见故障

`POST /callback` 返回 `403` 表示请求已经到达后端，但在验签、解密或账号白名单校验时被拒绝。日志中的安全错误码可按下列方向处理：

- `wechat_kf_callback_signature_invalid`：后台回调 Token 与 `.env` 不一致。
- `wechat_kf_callback_receiver_invalid`：`WECHAT_KF_CORP_ID` 不是当前企业的 CorpID。
- `wechat_kf_callback_padding_invalid`：EncodingAESKey 不一致。
- `wechat_kf_open_kfid_not_allowed`：回调中的 API `OpenKfId` 未配置在 `WECHAT_KF_OPEN_KFID(S)`。

`POST /callback` 返回 `200` 只表示回调已验签并进入异步同步流程，不代表消息已经拉取或回复。继续查看 `wechat_kf_sync_failed`、`wechat_kf_message_completed` 和 `last_send_at`。

若 `sync_msg` 返回 `wechat_kf_api_rejected:95012`，检查企业微信后台的完整授权链路：

1. 获取 Access Token 的 Secret 来自当前“可调用接口的应用”，而非旧系统应用或其他自建应用。
2. 目标客服账号已设置为通过该应用进行 API 管理。
3. 客服账号对应的全部接待人员位于该应用可见范围内。
4. 服务器公网出口 IP 已加入可信 IP，后台修改已保存并生效。

该错误由企业微信 API 返回，不能通过修改回调验签或绕过后端校验解决。排障时不要把 Secret、回调 Token、EncodingAESKey 或 Access Token 输出到终端截图和公开 Issue。

## 部署约束

当前回调在返回 `success` 后用进程内异步任务处理，适合第一版单 API worker 验证。不要用多个 Uvicorn worker；多实例生产部署前应把同步任务迁移到 Redis/Celery，并给同一 `OpenKfId` 加分布式锁。回调入口必须经网关限流，`backend/.env`、数据库、日志和上传目录必须限制访问权限。
