# 多模态接口说明

所有接口均位于 `/api/v1`，患者端请求使用 `X-Visitor-ID`。当前匿名访客标识仅用于第一版隔离，不替代生产身份认证。

## 上传图片

`POST /media/images`，请求类型为 `multipart/form-data`，字段名为 `images`，可重复提交多张图片。

成功返回媒体元数据列表。存储路径不会返回给客户端。

## 发送图片消息

先上传图片，再调用：

`POST /conversations/{conversation_id}/messages/stream`

```json
{
  "content": "这是正面和侧面的图片，请做一般科普说明",
  "attachment_ids": ["media-id-1", "media-id-2"],
  "input_mode": "multimodal"
}
```

文字可以为空；仅有图片时服务端使用中性的图片科普问题。附件必须属于当前访客、处于待绑定状态且尚未被其他消息使用。

## 读取图片

`GET /media/{media_id}/content`

只有媒体所属访客可以读取。响应使用 `private, no-store`，并禁止 MIME 嗅探。

## 删除

- 待发送媒体：`DELETE /media/{media_id}`。
- 已绑定媒体不能单独删除，删除所属会话时一并删除，避免历史记录出现不一致。
