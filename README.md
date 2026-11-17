## 医院耳畸形专科Agent智能助手

该项目为面向西安国际医疗中心（国内最大单体综合性医院）北院的整形医院对耳畸形儿童患者及家属的健康科普助手，依托医院的话术以及资料库，对其提供相关的一般知识、就诊准备以及术前术后的日常护理，不对病症进行诊断。

**患者端**：支持文本、图像、语音输入，流式回答并同步语音播报、历史会话管理等

**管理端**：支持按时间、用户ID导出对应交互内容，以xlsx形式；知识库支持增量更新；检索与评测支持在线单条检索，批量评测，查看召回率等各种指标，同时也能够进行多轮对话测试；支持公告管理，迭代更新能向患者端推送公告通知；支持接入企业微信H5，智能bot以及微信客服渠道。

> 目前这版上线为测试版，供医院医生使用、调试以及反馈。

## 1、项目结构
```
apps/
  web-h5/                 患者端
  admin-web/              管理端
backend/
  src/medical_agent/      FastAPI 应用
  migrations/             Alembic 数据库迁移
  scripts/                数据库、日志和知识库维护脚本
infra/compose/            PostgreSQL 与 Redis
tests/
  backend/                后端测试
  e2e/                    前端与语音链路测试
  load/                   并发压测
```
> 详细项目结构见 STRUCTURE.md

## 2、环境配置

### 1、基础软件安装
`winget install -e --id Git.Git`、`winget install -e --id Astral-sh.uv`、`winget install -e --id CoreyButler.NVMforWindows`、`winget install -e --id Docker.DockerDesktop`，安装完后重新打开终端，docker desktop首次启动时启用`WSL 2`，然后验证基础命令 `git --version`、`uv --version`、`nvm version`、`docker version`、`docker compose version`


### 2、安装 node.js 22 和 pnpm
```powershell
nvm install 22
nvm use 22
node --version
npm --version

corepack enable
corepack prepare pnpm@11.25.0 --activate
pnpm --version

# 如果当前 Node 安装不包含 Corepack，可使用：
npm install -g pnpm@11.25.0
```

### 3、使用uv安装python3.12
```powershell
uv python install 3.12
uv python list
uv --version
```
### 4、拉取项目
```powershell
git clone xxxx medical_agent
cd medical_agent
```

### 5、安装项目依赖

前端依赖：`cd medical_agent`、`pnpm install --frozen-lockfile`

后端依赖：`cd medical_agent\backend`、`uv sync --extra dev --frozen`

验证环境：`uv run python --version`、` uv run python -c "import fastapi; print(fastapi.__version__)" `

### 6、创建环境配置
复制根目录下的 `.\.env.example` 到对应backend下作为 `.env` 并依照里边内容添加对应的apikey、baseurl、model等等

### 7、选择数据库

（1）最小开发用本地sqlite，不用动，保持：
```dotenv
DATABASE_URL=sqlite+aiosqlite:///./medical_agent.db
REDIS_ENABLED=false
```


（2）PostgreSQL + Redis，先确认 Docker Desktop 已启动，然后执行：
```powershell
cd D:\medical_agent
docker compose -f infra/compose/compose.database.yaml up -d
docker compose -f infra/compose/compose.database.yaml ps
```
将 `backend/.env` 改为：
```dotenv
DATABASE_URL=postgresql+asyncpg://medical_agent:medical_agent_local@127.0.0.1:55432/medical_agent
REDIS_ENABLED=true
REDIS_URL=redis://127.0.0.1:56379/0
REDIS_KEY_PREFIX=medical-agent
```

查看容器状态或停止环境：

```powershell
docker compose -f infra/compose/compose.database.yaml ps
docker compose -f infra/compose/compose.database.yaml logs --tail 100 postgres redis
docker compose -f infra/compose/compose.database.yaml down
```

### 8、初始化数据库
首次启动前 / 迁移数据库 都需要执行：`cd D:\medical_agent\backend`、`uv run python scripts/upgrade_database.py`

### 9、启动三个服务(根目录)
启动后端前先运行docker dasktop
启动后端：`uv run uvicorn medical_agent.main:app --reload --host 127.0.0.1 --port 8000`
启动患者端：`pnpm dev:h5 -- --host 0.0.0.0`
启动管理端：`pnpm dev:admin -- --host 127.0.0.1`

默认访问地址：

- 后端健康检查：`http://127.0.0.1:8000/api/v1/health/live`
- 患者端：`http://127.0.0.1:5173`
- 管理端：`http://127.0.0.1:5174` (用 `.env`的 `ADMIN_API_TOKEN` 登录)


