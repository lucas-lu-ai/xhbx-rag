# xhbx-rag

`xhbx-rag` parses uploaded sales insight artifacts into RAG-ready files.

## Setup

```bash
uv sync
```

## Parse Sales Insights

```bash
uv run xhbx-rag parse \
  --insights uploads/case.sales_insights.json \
  --playbook uploads/case.sales_playbook.md \
  --out parsed
```

`--playbook` is optional. The parser does not call the upstream `xhbx` project, does not create embeddings, and does not write a vector database.
