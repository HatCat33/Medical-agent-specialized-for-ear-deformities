# 企业微信 H5 图片多选接入

患者 H5 在企业微信客户端内优先使用 JS-SDK 的 `chooseImage` 与 `getLocalImgData`。这条链路支持明确选择相册、按剩余数量多选，并继续复用项目现有的图片压缩、预览、上传、历史记录和随会话删除逻辑。

## 企业微信后台

1. 在企业微信管理后台创建或选择一个自建应用，记录企业 ID、AgentId 和应用 Secret。
2. 在应用的网页授权及 JS-SDK 配置中填写患者 H5 的正式 HTTPS 域名，并按企业微信要求完成域名校验。图片能力必须配置 **JS-SDK 可信域名**；只有使用 OAuth 获取成员身份时才必须配置网页授权域名。
3. 确认应用可见范围包含实际测试成员。JS-SDK 域名必须与患者访问页面的域名一致，不能填写带路径的地址。

后台通常只填写域名本身，例如 `medical.example.com`，不填写 `https://` 和页面路径。临时穿透域名发生变化后需要重新配置；如果企业微信不接受临时域名的所有权校验，应使用已完成校验的正式域名。

智能机器人 API 模式的 `WECOM_BOT_SECRET`、微信客服的 `WECHAT_KF_SECRET` 与这里的自建应用 `WECOM_SECRET` 不能互换。

## 后端配置

在 `backend/.env` 添加：

```dotenv
WECOM_H5_ENABLED=true
WECOM_CORP_ID=wwxxxxxxxxxxxxxxxx
WECOM_AGENT_ID=1000002
WECOM_SECRET=自建应用Secret
WECOM_H5_ALLOWED_ORIGINS=https://medical.example.com
WECOM_H5_API_BASE_URL=https://qyapi.weixin.qq.com
WECOM_H5_REQUEST_TIMEOUT_SECONDS=15
```

同时确保 `CORS_ORIGINS` 包含相同的患者 H5 来源。修改后重启后端。

## 工作方式与回退

- 企业微信客户端：先完成企业级 `wx.config` 和自建应用级 `wx.agentConfig` 权限注入，再检查 `chooseImage` 与 `getLocalImgData` 是否可用；“从相册选择”最多选择当前还允许添加的图片数量。
- 支持 File System Access API 的普通浏览器：使用浏览器直接文件选择能力。
- 不支持以上能力的旧 Android WebView：回退为一次系统文件选择器。纯 H5 无法修改该系统窗口的样式。
- JS-SDK 未配置、签名失败或域名不匹配：患者端显示提示并回退系统选择器，图片上传入口不会完全失效。

后端使用企业 JSAPI ticket 和应用 `agent_config` ticket 分别生成短期签名，只向前端返回签名参数，不返回应用 Secret、access token 或 ticket。签名只允许用于 `WECOM_H5_ALLOWED_ORIGINS` 中的页面来源。
