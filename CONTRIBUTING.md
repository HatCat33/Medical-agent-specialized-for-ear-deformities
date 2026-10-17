# 贡献与版本管理规范

## 分支规则

- `main`：最终验收交付的稳定版本，禁止直接开发和直接推送。
- `medical_agent_26_MM_DD`：按日期留存的阶段迭代分支，长期保留。
- `feature/<short-name>`：可选的短期功能分支，合并后可以删除。
- `fix/<short-name>`：可选的问题修复分支。

每个阶段分支应包含可运行代码、配置示例、启动说明、测试记录、当前限制和已知问题。重要阶段使用 `v26.MM.DD` 标签。

## 提交信息

推荐使用 Conventional Commits，并标明所属阶段：

```text
feat(stage-5): 增加会话流式输出
fix(stage-5): 修复 Markdown 段落间距
docs(stage-5): 补充部署和测试记录
test(stage-5): 增加流式中断回归测试
```

常用类型：`feat`、`fix`、`docs`、`test`、`refactor`、`chore`、`ci`。

## 开发流程

1. 从最新阶段分支创建功能分支。
2. 保持改动聚焦，新增行为时同步补充测试或验证记录。
3. 执行前后端质量检查。
4. 检查暂存文件，确认没有密钥、数据库、医院资料和患者数据。
5. 提交 Pull Request，由代码所有者审核后合并。
6. 阶段完成后更新 `CHANGELOG.md` 和 `docs/releases/`，再创建标签。

## 模块边界

- 医疗安全判断统一放在 `backend/src/medical_agent/modules/safety`。
- 飞书、企微等渠道 SDK 对象只能存在于 `modules/channels`，进入会话服务前必须转为内部契约。
- 模型厂商差异封装在 `providers`，业务模块不直接依赖厂商 SDK。
- 提示词、知识发布、模型发布和安全策略均作为版本化资产管理。
- 修改权限、安全、检索、发布或渠道回调时必须增加或更新测试。

## 提交前检查

```powershell
cd F:\medical_agent\backend
uv run pytest
uv run ruff check .
uv run pyright

cd F:\medical_agent
pnpm lint
pnpm typecheck
pnpm build
git status --short
git diff --cached --name-only
```

禁止提交医院原始资料、患者身份信息、未脱敏聊天记录、`.env`、API 密钥、本地数据库、运行日志、模型权重或生成媒体。
