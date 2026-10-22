# 切换到 Docker PostgreSQL

前提：Windows Docker Desktop 已启动并使用 Linux 容器。`docker version` 必须同时返回 Client 和 Server。当前工作环境无法启动 Docker Windows 服务，因此此配置已完成静态校验，实际 PostgreSQL 迁移尚未执行；不要把下面命令当作已完成的迁移记录。

## 启动数据库

```powershell
cd F:\medical_agent
docker compose -f infra/compose/compose.database.yaml up -d --wait
```

独立 Compose 项目 `medical-agent-db`，仅绑定 `127.0.0.1:55432`，使用持久化卷，不占用参考项目的 5432。默认密码 `medical_agent_local` 仅供本机开发；部署时通过 `POSTGRES_PASSWORD` 设置自有密码并在连接串中 URL 编码。

## 迁移已有数据

先停止当前后端，避免迁移期间继续收到会话写入。保留原始 SQLite 文件和 `backend/data/uploads` 媒体目录。

```powershell
cd F:\medical_agent\backend
$env:DATABASE_URL='postgresql+asyncpg://medical_agent:medical_agent_local@127.0.0.1:55432/medical_agent'
uv run alembic upgrade head
uv run python scripts/migrate_sqlite_to_postgres.py
uv run python scripts/migrate_sqlite_to_postgres.py --apply
uv run alembic check
uv run uvicorn medical_agent.main:app --host 127.0.0.1 --port 8000 --workers 1
```

第一条脚本命令只预览行数，`--apply` 才复制。脚本使用 SQLite 备份接口得到一致快照；按外键顺序复制所有现有业务表（会话、消息、媒体元数据、知识版本、chunk、任务/评测记录），并核对每张表行数。目标业务表非空时拒绝操作，失败时回滚复制事务，不覆盖目标数据，也不修改原始 SQLite。

图片文件不在数据库中，继续保留原来的 `MEDIA_STORAGE_PATH`。确认管理端总览显示 `postgresql`、知识条目数和历史会话正常后，把相同 `DATABASE_URL` 写入本地 `backend/.env`。环境变量优先于 `.env`；不要把含密码的本地配置上传 Git。

回退时停止后端，恢复原 SQLite 连接串并重启。迁移后 PostgreSQL 新写入的数据不会自动回流到旧 SQLite，正式切换期间应保持维护窗口。

## 结构迁移与运行

生产环境 `APP_ENV=production` 不自动建表，发布前显式执行 `uv run alembic upgrade head`。开发环境继续兼容旧 SQLite，启动时仅补建不存在的表。后续字段变更需要新增 Alembic revision，不修改冻结的 `0001` 快照。

```powershell
docker compose -f F:\medical_agent\infra\compose\compose.database.yaml ps
docker compose -f F:\medical_agent\infra\compose\compose.database.yaml logs --tail 100 postgres
docker compose -f F:\medical_agent\infra\compose\compose.database.yaml stop
```

`stop` 保留数据卷。不要使用 `down -v`，它会删除数据库卷。当前阶段不需要为管理台额外启动 Redis 或 MinIO。
