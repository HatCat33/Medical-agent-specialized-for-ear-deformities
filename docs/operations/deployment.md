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
- 医院内容审核、安全评测、并发压测和合规确认。

`infra/compose/compose.prod.yaml` 当前只是生产配置占位，不能视为已完成的生产部署方案。
