# 测试说明

## 声音设置专项验收

连续播报与手机点击反馈：`pnpm test:e2e:continuous-speech`（默认患者端 `https://127.0.0.1:5173`，可用 `E2E_BASE_URL` 覆盖）。脚本使用合成会话和本地 WAV 音频拦截接口，检查播放期间已请求后续音频、原生播放器段间衔接、手机无蓝色高亮、键盘焦点可见，不调用真实云端模型。单元测试另外覆盖并发上限、乱序返回、短句合并、取消与预加载失败。真实网络或供应商合成慢于播放时仍可能缓冲。

`node tests/e2e/speech-admin-smoke.mjs` 使用 `ADMIN_E2E_BASE_URL`（默认 `http://127.0.0.1:5174`）和环境中的 `ADMIN_E2E_TOKEN`。当前场景要求配置硅基流动 CosyVoice 预设列表。默认检查桌面/手机模型入口、试听错误详情及模拟失效在线音色的本机回退，不保存或覆盖声音设置。设 `E2E_REAL_TTS=1` 时额外发送一次真实云端试听请求并等待浏览器音频 `playing` 事件，产生少量供应商调用；这证明音频可解码并进入播放状态，不等于验证扬声器实际出声。截图保存在 `.artifacts/visual-check`。

## 后端

```powershell
cd F:\medical_agent\backend
uv run pytest
uv run ruff check .
uv run pyright
```

后端测试覆盖基础医疗边界、会话生命周期、访客隔离、流式事件、同会话滚动摘要与图片上下文、媒体归属校验、附件历史、文件生命周期、多模态模型消息格式、临时音频转写、提示词发布/回退与运行时生效、模型配置脱敏和探活记录，以及企业微信机器人/微信客服的消息解析、去重与同步链路。

## 前端

```powershell
cd F:\medical_agent
pnpm test
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
pnpm test:e2e:playback
pnpm test:e2e:continuous-speech
pnpm test:e2e:admin
```

- `stream`：确认浏览器在完整回答结束前收到增量内容。
- `visual`：检查桌面、手机、多图预览、图片消息、历史侧栏和消息布局。
- `markdown`：检查段落、标题、列表、引用和横向溢出。
- `voice`：使用模拟麦克风验证按住录音、松开转写和自动发送。
- 录音回归同时检查取消手势、麦克风音量声波与识别阶段展示。供应商识别结果由测试桩提供，不冒充真实语音识别准确率验收。
- `playback`：模拟浏览器音色和合成问答，检查电脑/手机自动播报、静音持久化、不重播历史和顶部布局。默认连接 `https://127.0.0.1:5183`，可用 `E2E_BASE_URL` 改为当前 H5 地址；接口全部拦截，不读写患者数据。
- `continuous-speech`：检查云端语音预加载、播放顺序、连续衔接和移动端触摸反馈。
- `admin`：在独立测试数据库上检查知识、评测、提示词、模型探活、声音设置和操作审计。
- 管理端独立验收命令见下方，使用隔离数据库和真实 API。

Demo 测试版包含 FAQ 冲突检测、发布版本检索、RAG 上下文/引用链路、渠道消息和会话摘要测试。当前仍需补充医院审核问题集上的 Recall@K、无答案拒答率、引用准确率和医学事实一致性评测，不能用单元测试替代医学验收。

## 管理端真实接口验收

在三个终端分别执行。测试会创建、发布、编辑和删除验收条目，**只允许使用独立测试库**，不指向真实医院数据库。测试代码要求 `e2e-admin-only` 令牌、SQLite 且 Embedding 未配置。

终端一：

```powershell
cd F:\medical_agent\backend
New-Item -ItemType Directory -Force ..\.artifacts | Out-Null
$env:DATABASE_URL='sqlite+aiosqlite:///F:/medical_agent/.artifacts/admin-smoke.db'
$env:ADMIN_API_TOKEN='e2e-admin-only'
$env:LLM_PROVIDER='demo'
$env:EMBEDDING_BASE_URL=''
$env:EMBEDDING_API_KEY=''
$env:EMBEDDING_MODEL=''
$env:RAG_ENABLED='true'
$env:INTERACTION_LOG_ENABLED='false'
uv run uvicorn medical_agent.main:app --host 127.0.0.1 --port 8011
```

终端二：

```powershell
cd F:\medical_agent
$env:VITE_BACKEND_PROXY_TARGET='http://127.0.0.1:8011'
pnpm --filter @medical-agent/admin-web dev --host 127.0.0.1 --port 5184 --strictPort
```

终端三：

```powershell
cd F:\medical_agent
pnpm test:e2e:admin
```

覆盖电脑/手机统一顶栏、登录、空库建草稿、chunk 增删改查、检索、从标准问题自动生成评测集、评测报告、系统/业务提示词发布、总览中的全模型探活入口、音色设置持久化、系统审计、发布只读保护和复制版本。截图位于 `.artifacts/visual-check`，均为合成数据。后端 `test_knowledge_admin.py` 另覆盖 XLSX 增量幂等、分类均衡抽样、其他问法映射、向量复用/失效、失败续跑、任务互斥、版本回退、模型变更保护及评测指标；`test_system_admin.py` 覆盖提示词版本、声音设置权限和模型运维接口；`test_prompt_migration.py` 验证已有提示词升级后保留内容。
