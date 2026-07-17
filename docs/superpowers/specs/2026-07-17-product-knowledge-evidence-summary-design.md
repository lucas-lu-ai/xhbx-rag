# 产品知识引用摘要展示设计

## 目标

当 Web 问答检索到 `knowledge_entry` 产品知识时，右侧“引用明细”顶部内容区显示该 chunk 的“摘要”和“关键要点”，不再错误显示“暂无异议处理内容”。

## 根因

后端 `_retrieval_evidences_for_ui()` 会完整保留检索结果中的 `text`，产品知识正文没有在接口层丢失。前端 `EvidenceDetail` 当前无条件调用 `EvidenceObjectionText`，该组件只提取“客户异议、异议诊断、推荐回应”三个字段；`knowledge_entry` 使用“摘要、关键要点”等字段，因此必然落入异议内容空状态。

下方“原文摘录”来自当前 citation 的 `display_excerpt`，只是单条来源摘录，不等同于完整 chunk 摘要。

## 范围

- `knowledge_entry`：顶部内容区固定显示“摘要、关键要点”。
- `objection_handling` 及现有其他类型：保持当前异议三字段展示行为不变。
- 不修改后端响应、Milvus 数据、索引脚本或存储格式。
- 不恢复此前已移除的完整通用结构化详情、标签或关联话术界面。

## 前端设计

### 文本解析

在 `evidenceText.ts` 的结构化字段白名单中加入产品知识所需字段：

- `摘要`：解析为单值字段。
- `关键要点`：解析为 bullet 块。

### 分类型渲染

`EvidenceDetail` 根据 `evidence.chunk_type` 选择内容组件：

- `knowledge_entry` 使用产品知识摘要组件，按固定顺序显示“摘要”后“关键要点”。
- 其他类型继续使用现有 `EvidenceObjectionText`，保持“客户异议、异议诊断、推荐回应”的固定顺序和现有空状态。

产品知识摘要组件只消费解析后的字段，不重新使用正则解析原文，避免出现第二套格式规则。

### 空状态

如果 `knowledge_entry` 同时缺少有效“摘要”和“关键要点”，显示“暂无知识摘要。”。该兜底与异议知识的“暂无异议处理内容。”明确区分。

### 样式

复用现有 `.evidence-text`、`.evidence-struct`、`.evidence-field-label` 和列表样式，不新增布局或颜色体系。内容区继续保持最大高度和纵向滚动，避免关键要点过长撑开右侧面板。

## 测试

在 `EvidenceDetail.test.tsx` 增加产品知识用例并保留现有异议用例：

1. `knowledge_entry` 只显示“摘要、关键要点”，且顺序固定。
2. 标题、分类、标签、来源原文不出现在顶部摘要区。
3. 缺少摘要和关键要点时显示“暂无知识摘要。”，不显示异议空状态。
4. `objection_handling` 继续只显示“客户异议、异议诊断、推荐回应”。

同时运行 `evidenceText` 解析测试、`EvidenceDetail` 组件测试、完整 Web 测试和生产构建。
