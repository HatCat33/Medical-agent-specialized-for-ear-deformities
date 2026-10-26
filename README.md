# 耳畸形专科医疗科普问答智能体

面向医院患者及家属的耳畸形健康科普问答系统。当前 `0.4.0` 为 **Demo 测试版**：在患者端多模态问答、历史会话和版本化 RAG 管理基础上，加入同会话滚动摘要、图片上下文、企业微信智能机器人 API 模式与微信客服回调渠道，用于经理演示和阶段验收。

> 本项目只提供健康科普信息，不能替代医生面诊、诊断或治疗建议。Demo 测试版仅用于受控演示和功能验证，不是可直接承载真实患者数据的生产系统。

## 当前状态

已实现：

- React 移动端 H5，支持桌面与手机浏览器。
- 单行输入区；手机统一使用系统图片选择器，桌面端额外支持摄像头弹窗拍照。
- 桌面历史侧栏可收起并记忆状态；移动端支持按钮或从左侧区域右滑，以 Push 推开方式进入历史会话，支持跟手拖动、左滑/点击遮罩返回。
- FastAPI 会话接口与 SQLite / Docker PostgreSQL 持久化。
- 新建、查看、删除历史会话，以及匿名访客隔离。
- OpenAI-compatible Chat Completions 模型接入。
- 从模型到浏览器的 SSE 流式输出、停止生成和部分回答保存。
- Markdown 安全渲染，不执行模型返回的原始 HTML。
- 单次最多四张 JPG、PNG、WebP、HEIC 或 HEIF 图片；手机系统统一提供拍照或相册来源，支持图片加文字的视觉问答。
- 图片私有存储、访客归属校验、历史回看及随会话删除。
- 按住录音、松开转写并自动发送；回答生成时按完整句流式播报，支持设备音色或兼容接口的云端 TTS。
- 请求、媒体、会话和模型事件的结构化轮转日志。
- 独立问答追踪日志，将输入、输出、模型、RAG 命中、耗时、状态及错误关联到同一次交互。
- 医院 FAQ XLSX 只读校验、冲突检测、版本发布及关键词/可选向量混合检索。
- 顶部导航管理端：运行总览、知识版本、chunk 查看/编辑/删除、XLSX 增量合并、后台向量任务、发布回退与操作记录。
- 管理端检索实验与批量标注评测：默认从标准问题按分类均衡生成，支持命中率、Recall@K、MRR、无结果准确率、报告保存和导出。
- 版本化提示词管理：系统与业务提示词均支持草稿、发布和历史回退，患者问答实时采用当前发布版本；独立医疗风险规则仍由安全模块执行。
- 文本、视觉、ASR、Embedding 与浏览器 TTS 配置总览，以及显式手动模型探活；API Key 不返回管理端。
- Docker PostgreSQL 部署、Alembic 结构迁移、SQLite 数据复制及逐表行数核对。
- 检索上下文与生成模型解耦，后端保留知识来源关联供评测和排障，患者端不展示参考资料；低召回时可禁止通用模型补答。
- 急症提示以及诊断、分型、处方和手术判定请求的基础边界控制。
- 无模型配置时可运行的 `demo` 模式。
- 当前会话超过原文窗口后自动滚动压缩，保留最近原文、历史摘要和最近图片；不建立跨会话长期记忆。
- 企业微信智能机器人 API 模式，以及微信客服加密回调、增量拉取、消息去重和文本回复。

尚未实现：

- PDF、视频转写内容的解析、切分、审核和 RAG 检索。
- 多级审核审批、独立管理员账户和角色权限。
- 医院内网私有化 ASR、语音原文件历史和医生音色克隆播报。
- 视频上传、转写和知识抽取。
- 飞书事件回调，以及企业微信渠道的主动消息、卡片、视频和文件处理扩展。
- 生产级身份认证、限流、审计、数据保留与监控告警。

本次范围见 [Demo 测试版发布记录](docs/releases/2026-10-26-demo.md) 和 [Demo 演示检查清单](docs/operations/demo-checklist.md)；RAG 基线见 [第三版 RAG 范围](docs/product/v3-rag-scope.md) 与 [RAG 架构说明](docs/architecture/rag.md)。

## 技术架构

```text
患者浏览器 / 内嵌 H5
        |
        v
React + Vite  ----SSE---->  FastAPI API
                              |-- safety 医疗边界
                              |-- conversation 会话与历史
                              |-- media 图片与授权读取
                              |-- speech 语音转写
                              |-- knowledge FAQ 发布与混合检索
                              |-- providers 文本/视觉/ASR/Embedding 适配层
                              `-- SQLite 或 Docker PostgreSQL + 私有媒体目录

后续：pgvector 原生近邻索引 / Redis 多进程任务队列 / 对象存储
```

主要目录：

- `apps/web-h5`：患者端问答与历史会话。
- `apps/admin-web`：顶部导航管理端，包含知识、评测、提示词和模型运维。
- `backend`：FastAPI 模块化单体、模型适配器、迁移与测试。
- `prompts`：版本化回答、安全、查询改写和医生风格提示词。
- `evals`：脱敏评测集、规则、基线和报告。
- `infra`：容器、网关、可观测性与部署配置。
- `docs`：架构、数据治理、安全、测试、运维和版本记录。

完整职责说明见 [STRUCTURE.md](STRUCTURE.md)。

## 环境要求

- Python `3.12`
- [uv](https://docs.astral.sh/uv/)
- Node.js `20`
- pnpm `11.25.0`

## 本地启动

### 1. 启动后端

```powershell
cd F:\medical_agent\backend
if (!(Test-Path .env)) { Copy-Item ..\.env.example .env }
uv sync --extra dev
uv run uvicorn medical_agent.main:app --reload --host 127.0.0.1 --port 8000
```

默认是 `demo` 模式。健康检查地址：`http://127.0.0.1:8000/api/v1/health/live`。

经理演示前请按 [Demo 演示检查清单](docs/operations/demo-checklist.md) 完成数据库迁移、模型探活、知识版本确认、隐私检查和关键流程冒烟测试。

### 2. 启动患者端 H5

另开一个终端：

```powershell
cd F:\medical_agent
pnpm install --frozen-lockfile
pnpm dev:h5 -- --host 0.0.0.0
```

本机访问终端显示的 Local 地址。同一局域网设备使用终端显示的 Network 地址。`0.0.0.0` 是监听地址，不是浏览器访问地址；配置开发证书后地址协议为 HTTPS。

手机语音输入必须通过 HTTPS。运行 `pnpm setup:https -- -InstallWindowsCA` 生成局域网开发证书，并按 [运行与部署说明](docs/operations/deployment.md) 在测试手机上信任开发 CA；普通 HTTP 即使已配置跨域，也不能调用手机麦克风。

Vite 开发服务器只用于开发和演示，不应直接用于医院生产环境。公网部署要求见 [运行与部署说明](docs/operations/deployment.md)。

临时供外网测试时，可在前后端已启动的情况下使用 ngrok：

```powershell
& "F:\ragagent-main\ngrok\ngrok.exe" http https://127.0.0.1:5173 --host-header=rewrite
```

将 ngrok 输出的公网 HTTPS 地址发给测试人员即可。该方式只用于临时功能验证，不用于患者正式访问。

## 接入正式模型

编辑 `backend/.env`：

```dotenv
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=replace-me
LLM_MODEL=replace-me

VISION_BASE_URL=https://your-provider.example/v1
VISION_API_KEY=replace-me
VISION_MODEL=replace-with-a-vision-model

ASR_PROVIDER=openai-compatible
ASR_BASE_URL=https://your-provider.example/v1
ASR_API_KEY=replace-me
ASR_MODEL=replace-with-an-asr-model
ASR_LANGUAGE=
```

文本与视觉模型服务需兼容 OpenAI Chat Completions 流式接口，ASR 服务需兼容 `POST /audio/transcriptions`。图片请求只会发送到独立配置的视觉模型；未配置 `VISION_*` 时图片仍会进入历史，但不会误发给纯文本模型。`VISION_ENABLE_THINKING` 只应为明确支持该参数的视觉模型配置。密钥只能保存在本地环境变量、服务器密钥管理服务或 CI Secret 中，禁止提交到 Git。

## 导入医院 FAQ 并启用 RAG

先在 `backend/.env` 中启用 RAG。Embedding 为可选配置；留空时使用本地关键词检索，配置后使用关键词与向量混合检索：

```dotenv
RAG_ENABLED=true
RAG_TOP_K=4
RAG_MIN_SCORE=0.16
RAG_RELATIVE_SCORE=0.62
RAG_REQUIRE_MATCH=true

EMBEDDING_BASE_URL=https://your-provider.example/v1
EMBEDDING_API_KEY=replace-me
EMBEDDING_MODEL=replace-with-an-embedding-model
```

导入前先检查 FAQ，不会修改原 XLSX：

```powershell
cd F:\medical_agent\backend
uv run python scripts/profile_faq_xlsx.py "C:\path\to\hospital-faq.xlsx"
uv run python scripts/import_faq_rag.py "C:\path\to\hospital-faq.xlsx" --name "医院 FAQ 2026-10-22" --publish
```

如果 FAQ 已在配置 Embedding 前导入，可安全补全当前发布版本缺失的向量；命令可重复执行，已存在的向量不会重复计算：

```powershell
uv run python scripts/rebuild_faq_embeddings.py
uv run python scripts/search_rag.py "第一次就诊需要准备什么？"
```

只有更换 Embedding 模型时才使用 `--rebuild-all`。该参数会覆盖当前版本的全部已有向量：

```powershell
uv run python scripts/rebuild_faq_embeddings.py --rebuild-all
```

如果存在同一问题对应不同答案，默认拒绝发布。完成医学审核后，可以修订原文件再导入；也可显式使用 `--exclude-conflicts` 排除冲突行并发布其余内容。医院 XLSX、数据库及检索索引不得提交到 Git。

以后切换到自有微调模型时，只需让服务兼容 OpenAI Chat Completions，并替换 `LLM_BASE_URL`、`LLM_API_KEY` 和 `LLM_MODEL`。知识导入、检索、引用及安全模块无需重写。

## 知识库管理端

先在 `backend/.env` 设置独立管理员令牌并重启后端：

```dotenv
ADMIN_API_TOKEN=replace-with-a-long-random-token
```

再启动管理端：

```powershell
cd F:\medical_agent
pnpm dev:admin -- --host 127.0.0.1
```

访问 `http://127.0.0.1:5174`，输入管理员令牌。顶部统一导航提供运行总览、知识版本、chunk 内容管理、检索评测、提示词管理、声音设置和任务记录。运行总览集中展示聊天、视觉、ASR、Embedding、TTS 与各自的探活按钮。知识建议流程：复制草稿 → 导入/编辑 → 补全向量 → 评测 → 发布。系统提示词和业务提示词一同版本化，草稿保存不生效，发布后新问答立即采用；独立医疗风险规则仍由安全模块执行。提示词和声音设置修改均校验现有 `ADMIN_API_TOKEN`，并写入系统操作审计。模型地址脱敏，不返回 API Key；探活必须手动触发，视觉与 ASR 测试可能产生少量供应商调用。全量向量重建只允许草稿；管理员令牌保存在当前标签页的 `sessionStorage`。

已有数据库升级到本版时，在 `backend` 目录执行 `uv run python scripts/upgrade_database.py`，然后重新启动后端。SQLite 会先生成一致性备份；迁移兼容开发环境已创建声音设置表或上次迁移部分完成的状态，保留原提示词与已有设置。

详细说明见 [知识管理手册](docs/operations/knowledge-admin.md) 与 [Docker PostgreSQL 迁移](docs/operations/postgres-migration.md)。当前任务执行器使用单 API worker，任务与评测结果持久化到数据库。

## 媒体与日志

- 图片保存在 `MEDIA_STORAGE_PATH` 指定的私有目录，通过授权接口读取，不作为静态文件公开。
- 图片类型根据文件内容校验，不只信任扩展名；默认最多 4 张、单张 5 MB、总计 16 MB。
- 多轮记忆仅限当前会话：最近 20 条消息保留原文，更早内容每累计 8 条滚动压缩为会话摘要；未达到批次或摘要失败时旧消息仍按原文带入，不会产生记忆空档。历史图片从整个当前会话独立选择最近 4 张，不受文字窗口限制，也不会跨会话复用。
- 企业微信智能机器人 API 模式支持文字、语音转写、图片/图文混排输入和流式回复，无需公网回调地址；配置与单 worker 部署方法见 [企业微信机器人接入](docs/operations/wecom-bot.md)。
- 微信客服作为独立回调渠道支持客户文字、图片和 AMR 语音输入，复用现有 RAG、安全、多轮上下文、图片上下文、历史记录与问答日志；需要公网 HTTPS 回调、持久化游标和数据库迁移，详见 [微信客服接入](docs/operations/wechat-kf.md)。
- 浏览器只负责临时录音，后端调用 `ASR_*` 配置的模型完成转写；原始麦克风音频默认不落盘，只保存转写后发送的文字。
- 回答播报支持浏览器设备 TTS 或兼容接口的云端 TTS，不保存音频文件。新回答随模型流式输出按完整句尽早播报，云端音频按序预加载；顶部声音键记住静音偏好，历史消息不自动朗读。后台支持音色与播报参数、试听；云端通过 `TTS_*` 配置，支持填写供应商已创建的自定义/克隆音色 ID。创建或训练音色仍在供应商侧完成。部分手机浏览器需用户手势或音频权限才能播放。
- 应用日志写入 `LOG_FILE_PATH`，默认单文件 10 MB、保留 10 个轮转文件，不记录问题正文、转写文字或图片内容。
- 问答追踪日志写入 `INTERACTION_LOG_FILE_PATH`，默认是 `backend/logs/interactions.jsonl`；每行是一条完整 JSON 记录，可通过 `interaction_id`、`conversation_id` 和消息 ID 定位一次问答，并包含实际使用的提示词版本 ID 与校验值。
- 如在 `backend/.env` 启用 `INTERACTION_LOG_INCLUDE_CONTENT=true`，问答追踪日志会保存用户文字和模型完整回答；图片只记录媒体 ID、类型和数量，不保存二进制内容，原始录音仍不落盘。模板默认关闭正文记录。
- 生产环境应限制日志目录访问权限，并按医院要求设置 `INTERACTION_LOG_INCLUDE_CONTENT`、文件大小、轮转数量和销毁周期。关闭正文后仍保留长度、SHA-256、状态、耗时和错误信息。

查看最近的问答追踪记录：

```powershell
cd F:\medical_agent\backend
Get-Content .\logs\interactions.jsonl -Tail 20
```

## 质量检查

```powershell
cd F:\medical_agent\backend
uv run pytest
uv run ruff check .
uv run pyright

cd F:\medical_agent
pnpm lint
pnpm typecheck
pnpm build
```

前后端服务运行后，可执行端到端检查：

```powershell
pnpm test:e2e:stream
pnpm test:e2e:visual
pnpm test:e2e:markdown
pnpm test:e2e:voice
```

## 版本管理

- `main` 仅保存最终验收后的稳定版本，不直接开发。
- 阶段分支格式为 `medical_agent_26_MM_DD`。
- 当前 Demo 测试版为 `0.4.0`，阶段分支为 `medical_agent_26_10_26`，建议标签为 `v26.10.26-demo`。
- 第三版 RAG 基线仍保留在 `medical_agent_26_10_22`，标签为 `v26.10.22`；第二版稳定分支仍为 `medical_agent_26_10_18`。
- 功能开发可使用 `feature/<name>`，完成后合并回对应阶段分支。

详细规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据与安全

医院源文件、患者数据、凭据、日志、数据库、模型权重和生成媒体不得进入 Git。提交前请阅读 [SECURITY.md](SECURITY.md) 与 [数据处理规范](docs/security/data-handling.md)。

## 许可证

本仓库当前未设置开源许可证。除非项目权利方另行书面授权，仓库内容不代表授予复制、分发或商用许可。
