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
