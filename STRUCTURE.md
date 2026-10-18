# 项目结构

```text
medical_agent/
|-- apps/
|   |-- web-h5/                 # 患者端响应式 H5、历史会话与多模态交互
|   `-- admin-web/              # 医院知识审核和运营后台预留
|-- backend/
|   |-- src/medical_agent/
|   |   |-- api/                # API 聚合路由
|   |   |-- core/               # 配置、数据库、日志和中间件
|   |   |-- modules/            # 会话、媒体、语音、安全、知识和渠道模块
|   |   `-- providers/          # 文本、视觉、ASR 等外部模型适配器
|   |-- migrations/             # 数据库迁移
|   `-- tests/                  # 后端单元与集成测试
|-- packages/                   # API 客户端、设计令牌和共享工具预留
|-- prompts/                    # 版本化提示词和结构化输出定义
|-- evals/                      # 脱敏评测集、规则、基线和报告
|-- tests/e2e/                  # 流式、Markdown、视觉和语音浏览器检查
|-- infra/                      # HTTPS 脚本、容器、网关和部署配置
`-- docs/                       # 架构、API、安全、运维、产品和发布文档
```

## 模块边界

- 医疗安全判断只放在 `modules/safety`。
- 会话编排通过 `modules/conversation`，媒体归属和生命周期由 `modules/media` 管理。
- 语音转写接口由 `modules/speech` 暴露，厂商差异封装在 `providers/speech`。
- 飞书、企微等渠道对象只存在于 `modules/channels`，进入会话层前转换为内部契约。
- 运行数据、医院资料、患者数据、密钥、日志、数据库和上传媒体不进入 Git。

