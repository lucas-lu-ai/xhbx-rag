# xhbx-rag

`xhbx-rag` 用于解析上传的销售洞察产物，并生成后续 RAG 入库前可直接消费的文件。

当前第一版只处理以下文件：

- `case.sales_insights.json`
- `case.sales_playbook.md`（可选）

本项目不会调用上游 `xhbx` 项目，不会解析原始 `docx / pptx / pdf / txt` 素材，不会生成 embedding，也不会写入向量库。

## 环境准备

```bash
uv sync
```

## 解析销售洞察

```bash
uv run xhbx-rag parse \
  --insights uploads/case.sales_insights.json \
  --playbook uploads/case.sales_playbook.md \
  --out parsed
```

`--playbook` 可以省略。解析完成后会在 `parsed/<case_id>/` 下生成：

- `case.structured.json`：规范化后的结构化销售知识
- `chunks.jsonl`：面向后续向量检索的 RAG chunk
- `parse_report.json`：解析报告、统计信息和 warning/error

## 后续查询层预留

后续 Agentic RAG 查询层会基于这些解析产物继续扩展：

- 用户输入先做 query rewrite
- 结构化检索和向量检索混合召回
- 使用 AgentScope 2.0.3 的 `Agent + ReActConfig` 实现受控 ReAct 检索
- 回答必须基于证据和引用，证据不足时返回无法确认

## Milvus Lite 本地入库

解析得到 `chunks.jsonl` 后，可以先写入本地 Milvus Lite 做检索验证：

```bash
uv run xhbx-rag index \
  --chunks parsed/案例a_b2bb7fa579/chunks.jsonl
```

本地检索命令：

```bash
uv run xhbx-rag search \
  --query "客户不想聊保险怎么开场？" \
  --top-n 20 \
  --top-k 5
```

`search` 不会直接向量化原始 query。流程是：

1. 调用 query understanding，把原始问题改写成 `rewritten_query` 并抽取过滤条件。
2. 只向量化 `rewritten_query`。
3. 使用 Milvus Lite 召回 topN。
4. 使用 rerank API 重排。
5. 输出 topK evidence chunks。

## 环境变量

对话模型：

```env
API_KEY=
BASE_URL=
MODEL_NAME=
```

SiliconFlow embedding：

```env
EMBEDDING_BASE_URL=https://api.siliconflow.com/v1
EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-8B
EMBEDDING_API_KEY=
```

SiliconFlow rerank：

```env
RERANK_BASE_URL=https://api.siliconflow.com/v1
RERANK_MODEL_NAME=Qwen/Qwen3-Reranker-8B
RERANK_API_KEY=
```

Milvus Lite：

```env
MILVUS_LITE_PATH=.local/milvus/xhbx_rag.db
MILVUS_COLLECTION=xhbx_sales_chunks
MILVUS_VECTOR_DIM=
```

`MILVUS_VECTOR_DIM` 可以留空。首次入库时会根据 embedding 返回向量长度自动创建 collection。
