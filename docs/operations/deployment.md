# 运行与部署说明

## 开发与局域网演示

后端监听本机：

```powershell
cd F:\medical_agent\backend
uv run uvicorn medical_agent.main:app --reload --host 127.0.0.1 --port 8000
```

H5 监听全部网卡并由 Vite 代理 `/api` 到本机后端：

```powershell
cd F:\medical_agent
pnpm dev:h5 -- --host 0.0.0.0
```

同一局域网设备访问终端显示的 Network 地址。Windows 防火墙需要允许 TCP 5173 入站。此方式只用于开发和临时演示。

手机浏览器禁止普通 HTTP 页面使用麦克风。首次进行局域网语音调试时，在 PowerShell 执行：

```powershell
cd F:\medical_agent
pnpm setup:https -- -InstallWindowsCA
pnpm dev:h5 -- --host 0.0.0.0
```

脚本会为当前局域网 IP 生成证书并写入本地 `.env.local`。将 `.certs/dev-ca.crt` 安装到测试手机并设为信任，然后使用终端显示的 `https://电脑IP:5173`。如果自动选择的网卡不正确，可执行：

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/setup-dev-https.ps1 -LanIp 192.168.1.10 -InstallWindowsCA
```

开发后端同时允许 `localhost` 与 RFC 1918 私网来源跨域。生产环境不要使用私网正则，应通过 `CORS_ORIGINS` 明确列出正式 HTTPS 域名。CORS 只控制接口访问，不能让 HTTP 页面获得麦克风权限。

## 临时公网演示

外网设备无法访问 `10.x.x.x`、`192.168.x.x` 等局域网地址。前后端已按上文启动后，可临时使用 ngrok 暴露 H5：

```powershell
& "F:\ragagent-main\ngrok\ngrok.exe" http https://127.0.0.1:5173 --host-header=rewrite
```

测试人员访问 ngrok 输出的公网 HTTPS 地址。H5 的 `/api` 请求仍由 Vite 代理到本机 `127.0.0.1:8000`，因此后端必须保持运行。ngrok 地址可能在重启后变化；该方案不提供正式域名、稳定性、访问控制或生产安全保障。

## 公网与生产原则

生产环境不得直接暴露 Vite 开发服务器或 Uvicorn 开发进程。推荐拓扑：

```text
Internet
  -> HTTPS reverse proxy / load balancer
  -> static H5 assets
  -> /api to FastAPI workers
  -> PostgreSQL / Redis / object storage
```

上线前至少完成：

- 域名、HTTPS、可信代理和正确的 `CORS_ORIGINS`。
- 生产进程管理、健康检查、超时及 SSE 禁用代理缓冲。
- 身份认证、访问控制、限流、防滥用和审计。
- PostgreSQL 迁移、备份恢复和数据保留策略。
- 密钥管理、日志脱敏、监控告警和应急预案。
- 将 `MEDIA_STORAGE_PATH` 放在持久化加密磁盘或授权对象存储中，并验证备份与删除流程。
- 日志文件由 `LOG_FILE_PATH`、`LOG_MAX_BYTES` 和 `LOG_BACKUP_COUNT` 控制；生产环境应接入集中日志平台。
- 问答追踪日志由 `INTERACTION_LOG_*` 控制并写入独立文件；如保存问答正文，必须加密存储、限制运维人员权限，并在集中日志平台中建立单独索引和保留策略。
- 医院内容审核、安全评测、并发压测和合规确认。

语音输入使用浏览器麦克风采集临时录音，需要 HTTPS 安全上下文（`localhost` 开发环境除外）。录音由后端调用 `ASR_*` 配置的转写服务，正式医院环境必须完成该服务的数据路径评审，或切换为医院批准的私有 ASR。

`infra/compose/compose.prod.yaml` 当前只是生产配置占位，不能视为已完成的生产部署方案。

## 知识管理端与数据库更新（2026-10-21）

知识版本、chunk 编辑、后台向量任务及检索评测已接入管理端。使用步骤见 [知识管理手册](knowledge-admin.md)。当前后台任务执行器要求 API 单 worker，任务状态持久化在数据库中。

独立 Docker PostgreSQL 部署及 SQLite 数据复制步骤见 [数据库迁移](postgres-migration.md)。生产环境请先执行 `uv run alembic upgrade head`；`APP_ENV=production` 不自动建表。
