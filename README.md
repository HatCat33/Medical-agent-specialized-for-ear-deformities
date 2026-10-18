# 耳畸形专科医疗科普问答智能体

面向医院患者及家属的耳畸形健康科普问答系统。当前第二版 `0.2.0` 已完成文本、图片、语音和历史会话的基础闭环，为后续接入医院审核后的知识库、RAG 及飞书/企业微信渠道做准备。

> 本项目只提供健康科普信息，不能替代医生面诊、诊断或治疗建议。当前版本不是可直接承载真实患者数据的生产系统。

## 当前状态

已实现：

- React 移动端 H5，支持桌面与手机浏览器。
- 类豆包单行输入区；手机统一使用系统图片选择器，桌面端额外支持摄像头弹窗拍照。
- 桌面历史侧栏可收起并记忆状态，移动端使用抽屉式历史会话。
- FastAPI 会话接口与 SQLite 本地持久化。
- 新建、查看、删除历史会话，以及匿名访客隔离。
- OpenAI-compatible Chat Completions 模型接入。
- 从模型到浏览器的 SSE 流式输出、停止生成和部分回答保存。
- Markdown 安全渲染，不执行模型返回的原始 HTML。
- 单次最多四张 JPG、PNG、WebP、HEIC 或 HEIF 图片；手机系统统一提供拍照或相册来源，支持图片加文字的视觉问答。
- 图片私有存储、访客归属校验、历史回看及随会话删除。
- 按住录音、松开转写并自动发送，以及当前和历史回答的中文语音播报。
- 请求、媒体、会话和模型事件的结构化轮转日志。
- 急症提示以及诊断、分型、处方和手术判定请求的基础边界控制。
- 无模型配置时可运行的 `demo` 模式。

尚未实现：

- 医院 FAQ、PDF、视频转写内容的正式 RAG 检索与引用。
- 医院内网私有化 ASR、语音原文件历史和医生音色克隆播报。
- 视频上传、转写和知识抽取。
- 飞书、企业微信的事件回调、身份映射和正式上线配置。
- 医院后台的知识审核、发布与回滚界面。
- 生产级身份认证、限流、审计、数据保留与监控告警。

详细范围见 [第一版产品范围](docs/product/v1-scope.md)、[第二版产品范围](docs/product/v2-scope.md) 和 [第二版发布记录](docs/releases/2026-10-18-v2.md)。

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
                              |-- providers 文本/视觉/ASR 适配层
                              `-- SQLite + 私有媒体目录（开发验证）

后续：PostgreSQL + pgvector / Redis / 对象存储 / 异步任务
```

主要目录：

- `apps/web-h5`：患者端问答与历史会话。
- `apps/admin-web`：医院知识审核与运营后台预留。
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

## 媒体与日志

- 图片保存在 `MEDIA_STORAGE_PATH` 指定的私有目录，通过授权接口读取，不作为静态文件公开。
- 图片类型根据文件内容校验，不只信任扩展名；默认最多 4 张、单张 5 MB、总计 16 MB。
- 浏览器只负责临时录音，后端调用 `ASR_*` 配置的模型完成转写；原始麦克风音频默认不落盘，只保存转写后发送的文字。
- 回答播报使用浏览器本机 TTS，不生成或保存音频文件。
- 应用日志写入 `LOG_FILE_PATH`，默认单文件 10 MB、保留 10 个轮转文件，不记录问题正文、转写文字或图片内容。

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
- 当前第二版应用版本为 `0.2.0`，阶段分支为 `medical_agent_26_10_18`，发布标签为 `v26.10.18`。
- 功能开发可使用 `feature/<name>`，完成后合并回对应阶段分支。

详细规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据与安全

医院源文件、患者数据、凭据、日志、数据库、模型权重和生成媒体不得进入 Git。提交前请阅读 [SECURITY.md](SECURITY.md) 与 [数据处理规范](docs/security/data-handling.md)。

## 许可证

本仓库当前未设置开源许可证。除非项目权利方另行书面授权，仓库内容不代表授予复制、分发或商用许可。
