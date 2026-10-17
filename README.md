# 耳畸形专科医疗科普问答智能体

面向医院患者及家属的耳畸形健康科普问答系统。当前版本用于验证移动端 H5、流式问答、历史会话和基础医疗安全边界，为后续接入医院审核后的知识库、RAG、多模态输入及飞书/企业微信渠道做准备。

> 本项目只提供健康科普信息，不能替代医生面诊、诊断或治疗建议。当前版本不是可直接承载真实患者数据的生产系统。

## 当前状态

已实现：

- React 移动端 H5，支持桌面与手机浏览器。
- FastAPI 会话接口与 SQLite 本地持久化。
- 新建、查看、删除历史会话，以及匿名访客隔离。
- OpenAI-compatible Chat Completions 模型接入。
- 从模型到浏览器的 SSE 流式输出、停止生成和部分回答保存。
- Markdown 安全渲染，不执行模型返回的原始 HTML。
- 急症提示以及诊断、分型、处方和手术判定请求的基础边界控制。
- 无模型配置时可运行的 `demo` 模式。

尚未实现：

- 医院 FAQ、PDF、视频转写内容的正式 RAG 检索与引用。
- 图片、语音和视频输入，以及医生音色播报。
- 飞书、企业微信的事件回调、身份映射和正式上线配置。
- 医院后台的知识审核、发布与回滚界面。
- 生产级身份认证、限流、审计、数据保留与监控告警。

详细范围见 [第一版产品范围](docs/product/v1-scope.md) 和 [当前阶段发布记录](docs/releases/2026-10-17.md)。

## 技术架构

```text
患者浏览器 / 内嵌 H5
        |
        v
React + Vite  ----SSE---->  FastAPI API
                              |-- safety 医疗边界
                              |-- conversation 会话与历史
                              |-- provider 模型适配层
                              `-- SQLite（第一版）

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

本机访问 `http://127.0.0.1:5173`。同一局域网设备使用终端显示的 Network 地址。`0.0.0.0` 是监听地址，不是浏览器访问地址。

Vite 开发服务器只用于开发和演示，不应直接用于医院生产环境。公网部署要求见 [运行与部署说明](docs/operations/deployment.md)。

## 接入正式模型

编辑 `backend/.env`：

```dotenv
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=replace-me
LLM_MODEL=replace-me
```

模型服务需兼容 OpenAI Chat Completions 流式接口。密钥只能保存在本地环境变量、服务器密钥管理服务或 CI Secret 中，禁止提交到 Git。

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
```

## 版本管理

- `main` 仅保存最终验收后的稳定版本，不直接开发。
- 阶段分支格式为 `medical_agent_26_MM_DD`。
- 当前阶段分支为 `medical_agent_26_10_17`，建议标签为 `v26.10.17`。
- 功能开发可使用 `feature/<name>`，完成后合并回对应阶段分支。

详细规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 数据与安全

医院源文件、患者数据、凭据、日志、数据库、模型权重和生成媒体不得进入 Git。提交前请阅读 [SECURITY.md](SECURITY.md) 与 [数据处理规范](docs/security/data-handling.md)。

## 许可证

本仓库当前未设置开源许可证。除非项目权利方另行书面授权，仓库内容不代表授予复制、分发或商用许可。
