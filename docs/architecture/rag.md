# RAG 架构说明

## 请求链路

```text
用户问题
  -> 医疗安全规则
  -> 已发布知识版本
  -> 关键词检索 + 可选向量检索
  -> Top K 受控上下文
  -> OpenAI-compatible 文本/视觉模型
  -> 流式回答 + 来源引用
```

医疗安全规则先于检索和模型执行。被判定为紧急情况、诊断或处方请求时，不调用知识库替代安全响应。

## 模块边界

- `modules/ingestion/faq_xlsx.py`：只读解析和源文件质量检查。
- `modules/knowledge/models.py`：知识发布与条目持久化。
- `modules/knowledge/retriever.py`：混合评分和 Top K 选择。
- `modules/knowledge/service.py`：导入、发布、上下文和引用。
- `providers/embedding`：Embedding 厂商适配。
- `providers/llm`：当前 API 或后续微调模型适配。
- `modules/conversation`：组合安全、检索、模型和流式持久化。

## 模型替换

RAG 不依赖具体生成模型。自有微调模型只需实现兼容的 `/chat/completions` 流式接口，然后替换 `LLM_*` 配置。Embedding 模型和知识发布版本可以独立升级。

## 存储演进

当前支持 SQLite 和 Docker PostgreSQL，均保存知识正文与 JSON 向量，由应用层计算混合评分。已实现版本草稿、发布回退和管理操作记录。pgvector 原生向量列、HNSW/IVFFlat 索引和 Reranker 尚未实现，后续扩大量级时单独升级。

## 向量增量构建

`scripts/rebuild_faq_embeddings.py` 默认处理当前已发布版本中 `embedding_json` 为空的条目。每批成功后独立提交，因此进程中断后可直接再次运行。已有向量维度与新向量维度不一致时任务会停止；更换 Embedding 模型时应显式使用 `--rebuild-all`，避免混用不同模型生成的向量。

管理端以 `admin_router.py` / `admin_service.py` / `admin_schemas.py` 承载版本维护、后台任务和评测，避免把管理逻辑放入患者对话服务。管理端全量重建仅允许草稿；失败时阻止发布。`knowledge_operations` 保存任务进度、评测报告与操作记录。维护期间优先使用管理端，避免与独立 CLI 脚本同时操作同一版本。

详细操作见 [知识管理](../operations/knowledge-admin.md) 与 [PostgreSQL 迁移](../operations/postgres-migration.md)。
