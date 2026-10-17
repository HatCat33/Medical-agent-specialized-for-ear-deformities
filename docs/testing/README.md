# 测试说明

## 后端

```powershell
cd F:\medical_agent\backend
uv run pytest
uv run ruff check .
uv run pyright
```

后端测试覆盖基础医疗边界、会话生命周期、访客隔离、流式事件和模型 Provider 行为。

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
```

- `stream`：确认浏览器在完整回答结束前收到增量内容。
- `visual`：检查桌面、手机、历史侧栏和消息布局。
- `markdown`：检查段落、标题、列表、引用和横向溢出。

模型效果、医学准确性和 RAG 召回率尚未纳入第一版自动化基线；接入医院审核知识后必须建立独立评测集。
