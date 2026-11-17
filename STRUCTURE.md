# 项目结构说明

本文档描述当前 `medical_agent_26_11_17` 版本的实际目录、模块职责、运行数据边界和部署关系。目录说明以仓库当前代码为准

## 1. 顶层结构

```text
medical_agent/
├── apps/
│   ├── web-h5/                    患者端 H5
│   └── admin-web/                 管理端
├── backend/                       FastAPI 后端
│   ├── src/medical_agent/         后端源码
│   ├── migrations/                Alembic 数据库迁移
│   ├── scripts/                   维护、导入、导出和探活脚本
│   ├── pyproject.toml             Python 项目与工具配置
│   ├── uv.lock                    Python 依赖锁定文件
│   ├── .env                       服务器或本地私有配置，不提交
│   ├── data/                      上传媒体和运行数据，不提交
│   ├── logs/                      运行日志和交互日志，不提交
│   ├── backups/                   数据库或迁移备份，不提交
│   ├── exports/                   导出的业务文件，不提交
│   └── .venv/                     uv 创建的虚拟环境，不提交
├── infra/
│   ├── compose/compose.database.yaml  本地 PostgreSQL 与 Redis
│   └── scripts/setup-dev-https.ps1    可选的本地 HTTPS 脚本
├── tests/
│   ├── backend/                   后端单元和集成测试
│   ├── e2e/                       浏览器与端到端测试
│   └── load/                      并发和混合业务压测
├── .env.example                   环境配置模板
├── .env.local                     前端本地开发变量，不提交真实密钥
├── package.json                   pnpm 工作区脚本
├── pnpm-workspace.yaml            前端工作区定义
├── pnpm-lock.yaml                 前端依赖锁定文件
├── .nvmrc                         Node.js 主版本（22）
├── .python-version                Python 主版本（3.12）
├── README.md                      安装、启动、配置和部署入口文档
└── STRUCTURE.md                   本结构说明
```

### 运行时目录与源码目录

以下内容属于本机或服务器运行状态，不是发布源码：

- `backend/.env`：数据库、模型、管理端令牌和渠道密钥。
- `backend/.venv`：由 `uv sync` 创建。
- `backend/data/`：私有上传媒体等运行数据。
- `backend/logs/`：运行日志和交互日志。
- `backend/backups/`、`backend/exports/`：备份和业务导出文件。
- 根目录 `.artifacts/`、`.pytest_cache/`、`.ruff_cache/`、`node_modules/`。
- 根目录 `runtime/`、数据库文件和 `.certs/`：本地运行产物或开发辅助文件。
- `apps/*/dist/`：前端构建产物，发布服务器时使用，但不作为源码编辑入口。

这些目录不不在 Git 上，也不能在发布时用本地内容覆盖服务器同名数据目录。

## 2. 患者端 `apps/web-h5`

患者端是 React + Vite 应用，负责面向患者的会话、历史记录、多模态输入和回答播放。

```text
apps/web-h5/
├── src/
│   ├── app/App.tsx                应用外壳、会话状态和页面布局
│   ├── features/chat/             对话、消息、图片、录音和回答播报
│   ├── features/history/          历史会话侧栏、删除和移动端抽屉
│   ├── features/announcements/    患者端公告弹窗和公告中心
│   ├── lib/                       API、Markdown、图片、语音和播放工具
│   ├── styles/index.css           患者端全局样式和响应式布局
│   └── main.tsx                   React 入口
├── public/                        favicon 等静态资源
├── index.html                     Vite HTML 入口
├── vite.config.ts                 开发服务器和 API 代理
└── package.json                   患者端脚本和依赖
```

### 患者端关键模块

- `features/chat/ChatPanel.tsx`：消息列表、输入框、发送和流式响应。
- `features/chat/MessageBubble.tsx`：用户消息与模型消息的展示、交互菜单。
- `features/chat/MessageActionMenu.tsx`：复制、朗读等长按操作。
- `features/chat/MobileMediaPicker.tsx`、`CameraCapture.tsx`：移动端相册、拍照和多图选择。
- `features/chat/VoiceActivity.tsx`：录音状态、语音输入和发送前反馈。
- `features/chat/useAnswerSpeech.ts`：模型回答分段、TTS 请求和播放队列隔离。
- `features/chat/cloudPlayback.ts`、`pcmStreamPlayback.ts`、`playback.ts`：云端音频、PCM 流和浏览器播放。
- `features/history/HistorySidebar.tsx`：按最新时间排序的历史会话、用户 ID、公告入口。
- `features/announcements/AnnouncementCenter.tsx`：未读公告弹窗、列表和 Markdown 展示。
- `lib/api.ts`：患者端 REST/SSE 请求、媒体上传和会话接口。
- `lib/wecomImagePicker.ts`：企业微信 H5 JS-SDK 图片选择适配；未启用时回退系统选择器。

患者端不直接访问数据库、模型供应商或文件系统，所有业务数据都通过后端 API 获取。

## 3. 管理端 `apps/admin-web`

管理端是 React + Vite 应用，当前采用顶部导航。刷新时通过 URL hash 保留当前导航页面；无 hash 时默认进入“运行总览”。

```text
apps/admin-web/
├── src/
│   ├── app/App.tsx                登录、顶部导航和二次验证弹窗
│   ├── features/knowledge/        运行总览、知识库和检索评测
│   ├── features/system/           模型、并发、提示词、公告和系统操作
│   ├── lib/api.ts                 管理端 API、令牌和二次授权
│   ├── styles/index.css           管理端布局、表格、抽屉和滚动条
│   └── main.tsx                   React 入口
├── public/                        管理端图标等静态资源
├── index.html                     页面标题和 HTML 入口
├── vite.config.ts                 开发服务器和后端代理
└── package.json                   管理端脚本和依赖
```

### 管理端页面与组件

- `features/knowledge/KnowledgeDashboard.tsx`：统一承载五个一级页面。
- `features/knowledge/InteractionAnalyticsPanel.tsx`：按时间、用户 ID筛选交互内容、趋势和导出。
- `features/knowledge/VersionsPanel.tsx`：知识版本、草稿数量、导入审核和发布。
- `features/knowledge/EntriesPanel.tsx`：知识条目搜索、分页、编辑和向量状态。
- `features/knowledge/RetrievalPanel.tsx`：单条检索、批量评测和多轮对话测试。
- `features/knowledge/RecordNavigator.tsx`：交互记录详情及导出前确认。
- `features/system/ModelsPanel.tsx`：主模型、备用模型、视觉、ASR、Embedding、Rerank 和 TTS 配置。
- `features/system/CapacityPanel.tsx`：模型并发、排队和容量状态。
- `features/system/PromptsPanel.tsx`：系统提示词和业务提示词草稿、发布与历史。
- `features/system/AnnouncementsPanel.tsx`：公告编辑、Markdown 预览、草稿和发布。
- `features/system/SpeechPanel.tsx`、`SpeechDiagnostics.tsx`：语音设置、试听和探活信息。
- `features/system/SystemOperations.tsx`：系统探活和运维操作记录。
- `features/knowledge/ErrorNotice.tsx`：将错误显示在对应页面位置，而不是只显示顶部消息。

### 管理端安全边界

1. `ADMIN_API_TOKEN` 用于进入管理端。
2. `ADMIN_SECONDARY_TOKEN` 用于敏感写操作的二次验证。
3. 二次验证成功后的短时授权只保存在当前浏览器标签页，不保存令牌原文。
4. 交互记录查询和导出不要求修改权限；模型配置、知识发布、提示词、公告、重建和探活受二次验证保护。

## 4. 后端 `backend/src/medical_agent`

后端采用 FastAPI 模块化单体结构。`main.py` 创建应用和生命周期，`api/router.py` 汇总路由，各业务域在 `modules/` 中实现，外部模型在 `providers/` 中适配。

```text
backend/src/medical_agent/
├── main.py                        应用创建、启动、关闭和路由注册
├── api/
│   ├── router.py                  API 总路由
│   ├── dependencies.py            管理令牌、数据库和请求依赖
│   └── admin_security.py          管理端二次验证接口
├── core/
│   ├── config.py                  Settings、.env 读取和运行时配置覆盖
│   ├── database.py                SQLAlchemy 异步引擎、会话和数据库初始化
│   ├── redis.py                   Redis 连接、缓存和失效降级
│   ├── logging.py                 结构化日志、日期目录和保留策略
│   ├── middleware.py              请求日志、request_id 和统一异常链路
│   ├── cleanup.py                 日志和运行文件清理
│   └── admin_security.py          二次验证签名授权
├── modules/
│   ├── conversation/              会话、消息、SSE 流、摘要和历史
│   ├── knowledge/                 FAQ、知识版本、检索、评测和交互分析
│   ├── ingestion/                 XLSX 读取、校验和标准化
│   ├── media/                     图片上传、归属校验和授权读取
│   ├── speech/                    ASR、TTS、分段合成和播报设置
│   ├── system/                    模型配置、探活、容量、提示词和计数器
│   ├── announcements/             公告模型、接口和发布逻辑
│   ├── safety/                    医疗安全边界和系统级安全规则
│   └── channels/                  企业微信、微信客服和渠道公共契约
└── providers/
    ├── llm/                       文本/视觉 Chat Completions 适配
    ├── embedding/                 向量生成、批处理和缓存
    ├── rerank/                    Rerank 请求和结果归一化
    └── speech/                    ASR 外部接口适配
```

### 4.1 `core` 基础设施

- `config.py`：固定从 `backend/.env` 读取环境配置；管理端数据库运行时模型配置可覆盖部分模型字段。
- `database.py`：所有业务模块通过异步 SQLAlchemy 会话访问数据库；PostgreSQL 是服务器权威数据源。
- `redis.py`：缓存、幂等、限流和模型容量协调；Redis 故障时不替代 PostgreSQL，也不应存放唯一业务数据。
- `logging.py`：运行日志按北京时间年月日分目录，交互日志单独保存；正文记录由配置控制。
- `middleware.py`：为 HTTP 请求补充 `request_id`、耗时和状态日志，便于与交互日志关联。

### 4.2 业务模块

#### `conversation`

处理患者会话的创建、查询、删除和消息生成。文本、图片、语音转写后的输入最终都转换为统一的会话消息；模型回答通过 SSE 返回，部分回答和断线后的后台续答会保存到数据库。

#### `knowledge`

处理知识版本、草稿、XLSX 导入审核、重复问题、向量状态、混合检索、Rerank、单条检索、评测、多轮测试、交互分析和导出。患者端只接收回答，不接收知识库内部来源标记。

#### `ingestion`

`faq_xlsx.py` 负责读取医院 FAQ 表格、识别必需列、规范化日期和问题内容，并把导入内容交给知识库服务进行冲突比较；它不直接发布版本。

#### `media`

负责图片类型和大小校验、私有存储、访客/会话归属校验、历史读取以及删除级联。图片文件不应放在 Nginx 的公开静态目录中。

#### `speech`

负责语音输入转写、TTS 合成、流式音频响应、短句分段和语音服务设置。具体供应商协议由 `providers/speech` 或系统模型服务统一调用。

#### `system`

负责模型配置展示和运行时覆盖、模型探活、并发/排队状态、提示词版本、声音设置和累计交互计数器。API Key 只写入，不返回管理端。

#### `channels`

- `wecom/`：企业微信智能机器人 WebSocket 长连接。
- `wechat_kf/`：微信客服加密回调、消息同步、去重和客服账号校验。
- `wecom_h5/`：企业微信 H5 JS-SDK 配置和域名校验。
- `common/`：渠道事件、身份映射、收据和同步游标。

#### `safety`

保存医疗安全提示词、输出边界和安全响应结构。诊断、分型、手术、处方和个体化风险判断等限制不应通过普通业务提示词绕过。

### 4.3 外部模型适配层

业务模块不直接拼接供应商请求，统一通过 `providers/` 调用：

- `providers/llm/client.py`：主文本模型、备用模型、重试、流式输出和视觉请求。
- `providers/embedding/client.py`：Embedding 批量生成、查询合并和 Redis 缓存。
- `providers/rerank/client.py`：常见 `POST /rerank` 接口的请求和结果适配。
- `providers/speech/client.py`：OpenAI-compatible ASR 适配。
- TTS 由系统语音服务根据 `TTS_PROTOCOL` 选择 OpenAI-compatible、Azure Speech、ElevenLabs、火山引擎或 custom 协议。

## 5. 数据库与迁移

```text
backend/migrations/
├── env.py                         Alembic 异步迁移环境
├── script.py.mako                迁移脚本模板
└── versions/
    ├── 0001_initial.py           初始会话、消息和知识表
    ├── 0002_prompt_and_model_operations.py
    ├── 0003_prompt_system_and_speech.py
    ├── 0004_conversation_memory.py
    ├── 0005_channel_event_receipts.py
    ├── 0006_channel_sync_cursors.py
    ├── 0007_announcements.py     公告表
    └── 0008_system_counters.py   累计交互计数器
```

升级数据库：

```powershell
cd D:\medical_agent\backend
uv run python scripts/upgrade_database.py
```

`upgrade_database.py` 是项目推荐入口。它会执行尚未应用的 Alembic 迁移并进行当前应用所需的结构检查；不要手工删除服务器数据库表。

## 6. 维护脚本

| 脚本 | 用途 |
| --- | --- |
| `upgrade_database.py` | 执行数据库结构升级 |
| `migrate_sqlite_to_postgres.py` | 将本地 SQLite 数据迁移到 PostgreSQL |
| `profile_faq_xlsx.py` | 只读检查 FAQ XLSX 的列、日期和重复情况 |
| `import_faq_rag.py` | 导入知识内容并按参数发布版本 |
| `rebuild_faq_embeddings.py` | 补全或重建当前知识版本的向量 |
| `search_rag.py` | 从命令行验证知识检索 |
| `export_interactions_faq.py` | 将交互日志导出为医院 FAQ 格式 XLSX |
| `probe_tts_latency.py` | 测量 TTS 供应商延迟 |
| `serve_tts_probe.py` | 提供本地 TTS 探测辅助服务 |

脚本默认从 `backend/.env` 读取配置。含患者正文的导出文件只能保存到受控目录，不得提交到 Git。

## 7. 前后端请求关系

```text
患者端 / 管理端浏览器
        │
        │ REST、SSE、上传和音频流
        ▼
FastAPI /api/v1
        ├── conversation ── conversation/message/media 表
        ├── knowledge ───── knowledge release/entry/operation 表
        ├── speech ──────── ASR/TTS 供应商
        ├── system ──────── 模型配置、提示词、计数器
        ├── channels ────── 企业微信/微信客服 API
        ├── PostgreSQL ──── 权威业务数据
        ├── Redis ────────── 缓存、幂等、限流、容量租约
        └── backend/data ─── 私有图片和媒体文件
```

患者文本请求顺序：

1. 前端创建或确认会话并提交消息。
2. 后端校验访客、会话和幂等键。
3. 按当前发布提示词和知识版本执行检索/Rerank。
4. 通过主模型流式生成；首包前失败时按配置重试并尝试备用模型。
5. SSE 将回答片段返回浏览器，同时保存消息和交互追踪信息。
6. 前端按自然短句请求 TTS，音频按当前回答任务顺序播放。

图片请求会先经过媒体上传和归属校验，再由独立视觉模型处理。语音请求先经过 ASR，转写文本之后复用文本问答链路。

## 8. 测试目录

```text
tests/
├── backend/
│   ├── unit/                     配置、数据库、模型、日志和安全单元测试
│   └── integration/              会话、知识库和管理接口测试
├── e2e/
│   ├── patient/                  患者端流式、Markdown、图片和语音输入
│   ├── admin/                    管理端 UI、API 和语音设置
│   └── speech/                   TTS 播放、PCM 流和连续播报
└── load/
    ├── concurrent_chat.py       单一输入类型并发压测
    ├── mixed_workload.py        文本/语音/图片/TTS/会话操作混合压测
    └── test_metrics.py           压测指标计算测试
```

压测会调用真实模型供应商并可能产生费用。远程服务器压测必须显式允许远程地址，并避开医院真实患者使用时段。

## 9. 本地数据库与部署边界

### 本地

- SQLite 适合快速功能开发。
- `infra/compose/compose.database.yaml` 提供独立的 PostgreSQL 16 + pgvector 和 Redis 7。
- 本地数据库卷名为 `medical_postgres` 和 `medical_redis`，不要使用 `docker compose down -v`，除非确认可以删除数据。

### 服务器

正式后端目录：`/root/medical_agent/backend`。

正式前端静态目录：

- 患者端：`/root/My_OJ/hoj-deploy/standAlone/resources/medical-agent/patient-web`
- 管理端：`/root/My_OJ/hoj-deploy/standAlone/resources/medical-agent/admin-web`

服务器发布时只替换代码和构建产物，必须保留服务器自己的 `backend/.env`、数据库、Redis、`backend/data`、`backend/logs`、证书和企业微信验证文件。后端由 `medical-agent.service` 管理，端口和启动参数以服务器 systemd 服务文件为准。

## 10. 修改指南

- 修改页面布局：编辑对应应用的 `src/features` 或 `src/styles/index.css`。
- 修改患者问答流程：优先查看 `modules/conversation`，不要在前端拼接模型请求。
- 修改知识导入或检索：查看 `modules/ingestion` 和 `modules/knowledge`。
- 修改供应商请求格式：只改 `providers/` 或系统语音服务，不要在路由中复制供应商逻辑。
- 修改数据表：新增 Alembic migration，并同步更新模型、schema 和服务；不要直接改服务器数据库。
- 修改环境变量：同步更新 `core/config.py`、`.env.example` 和 README 中的说明。
- 修改 API 返回结构：同步更新前端 `lib/api.ts`、后端 schema 和相关测试。

完成修改后，至少运行：

```powershell
cd D:\medical_agent\backend
uv run ruff check src ..\tests\backend ..\tests\load
uv run pyright
uv run pytest

cd D:\medical_agent
pnpm typecheck
```

## 11. 安全边界

- 不提交 `.env`、API Key、管理员令牌、数据库、日志、医院原始 XLSX、患者信息、图片和音频。
- 不把 `backend/data` 暴露为 Nginx 静态目录。
- 不用本地数据库、日志或 `.venv` 覆盖服务器同名目录。
- 不直接删除服务器上一版项目和数据库备份，确认新版本正常后再按保留策略清理。
- 修改提示词不能绕过 `modules/safety` 中的医疗安全规则。
