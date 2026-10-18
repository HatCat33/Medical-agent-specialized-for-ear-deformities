# 测试说明

## 后端

```powershell
cd F:\medical_agent\backend
uv run pytest
uv run ruff check .
uv run pyright
```

后端测试覆盖基础医疗边界、会话生命周期、访客隔离、流式事件、媒体归属校验、附件历史、文件生命周期、多模态模型消息格式和临时音频转写。

## 前端

```powershell
cd F:\medical_agent
pnpm lint
pnpm typecheck
pnpm build
```

## 端到端检查

先启动后端和 H5，再执行：

```powershell
pnpm test:e2e:stream
pnpm test:e2e:visual
pnpm test:e2e:markdown
pnpm test:e2e:voice
```

- `stream`：确认浏览器在完整回答结束前收到增量内容。
- `visual`：检查桌面、手机、多图预览、图片消息、历史侧栏和消息布局。
- `markdown`：检查段落、标题、列表、引用和横向溢出。
- `voice`：使用模拟麦克风验证按住录音、松开转写和自动发送。

模型效果、医学准确性和 RAG 召回率尚未纳入第一版自动化基线；接入医院审核知识后必须建立独立评测集。
